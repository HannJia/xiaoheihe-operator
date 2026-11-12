"""Owner preview, durable approval and exactly-once news publishing."""
import asyncio
import json
from types import SimpleNamespace
from unittest import mock

import news_approval
from models import OutgoingStatus
from test_daily_news import _NewsCase, PLUGIN_MODULE, at


def message(user="10001", group=None, platform="qq", text="可以发送", timestamp=0):
    return {"platform": platform, "processed_plain_text": text, "message_info": {
        "user_info": {"user_id": user}, "group_info": group, "time": timestamp}}


class ApprovalTests(_NewsCase):
    def test_eight_am_queues_without_sleep_delay_or_automatic_send(self):
        async def scenario():
            plugin = self.make_plugin(mode="auto", activity="睡觉")
            plugin.config.news.post_time = "08:00"
            plugin.config.news.respect_sleep = False
            await plugin._maybe_daily_news(at(7,59))
            self.assertIsNone(self.row(plugin))
            await plugin._maybe_daily_news(at(8))
            self.assertEqual(self.row(plugin)['status'], OutgoingStatus.AWAITING_APPROVAL.value)
            self.assertIn("可以发送", self.send.texts[0])
            self.assertEqual(len(self.send.images),1)
            await plugin._maybe_daily_news(at(11,30))
            self.assertEqual(self.client.posts,[])
            self.assertEqual(self.client.uploads,[])
            self.assertEqual(plugin._store.count_since('news_post',at(0)),0)
            self.assertIsNone(plugin._store.last_outgoing_at())
        asyncio.run(scenario())

    def test_only_owner_private_qq_exact_phrase_can_approve(self):
        async def scenario():
            plugin=self.make_plugin(mode='auto')
            await plugin._maybe_daily_news(at(9))
            relayed=message()
            relayed['message_info']['additional_config']={'relayed_by_plugin':'some.plugin'}
            for msg in [message(user='other'),message(group={'group_id':'123'}),message(platform='web'),
                        message(text='不要可以发送'),message(text='转述：可以发送'),relayed,None]:
                result=await plugin.handle_news_approval_command(message=msg)
                self.assertFalse(result[0])
            self.assertIsNone(plugin._news_approval_task)
            with mock.patch.object(PLUGIN_MODULE['time'],'time',return_value=at(9,5)):
                result=await plugin.handle_news_approval_command(message=message())
                await plugin._news_approval_task
            self.assertTrue(result[0]);self.assertEqual(len(self.client.posts),1)
        asyncio.run(scenario())

    def test_repeated_and_concurrent_confirmation_never_duplicates(self):
        async def scenario():
            plugin=self.make_plugin(mode='auto')
            await plugin._maybe_daily_news(at(9))
            with mock.patch.object(PLUGIN_MODULE['time'],'time',return_value=at(9,5)):
                await asyncio.gather(*(plugin.handle_news_approval_command(message=message()) for _ in range(2)))
                await plugin._news_approval_task
                await plugin.handle_news_approval_command(message=message())
                await plugin._news_approval_task
            self.assertEqual(len(self.client.posts),1)
        asyncio.run(scenario())

    def test_restart_uses_the_same_saved_text_image_and_destination(self):
        async def scenario():
            plugin=self.make_plugin(mode='auto')
            await plugin._maybe_daily_news(at(9))
            approval=self.row(plugin)['metadata']['approval']
            original=news_approval.load(plugin._news_approval_dir(),approval['digest'])
            preview=news_approval.restore(original)
            restarted=self.make_plugin(mode='auto',store=plugin._store)
            restarted.config.news.post_target='PC 游戏'
            restarted.config.news.hashtags=['changed']
            self.client.resolve_topic_names=mock.AsyncMock(return_value={'盒友杂谈':'201'})
            await self.approve(restarted,at(12))
            self.assertEqual(len(self.client.posts),1)
            self.assertEqual(self.client.posts[0]['hashtags'],original['hashtags'])
            self.assertEqual(self.client.posts[0]['title'],preview.title)
            self.assertEqual([b['text'] for b in self.client.posts[0]['content_blocks'] if b['type']=='text'],preview.blocks)
            self.client.resolve_topic_names.assert_awaited_once_with(['盒友杂谈'])
        asyncio.run(scenario())

    def test_cancel_and_next_day_confirmation_do_not_publish(self):
        async def scenario():
            plugin=self.make_plugin(mode='auto')
            await plugin._maybe_daily_news(at(9))
            await self.approve(plugin,at(9,5,day=7))
            self.assertEqual(self.client.posts,[])
            await self.approve(plugin,cancel=True)
            self.assertEqual(self.row(plugin)['status'],OutgoingStatus.SUPPRESSED.value)
            await self.approve(plugin)
            self.assertEqual(self.client.posts,[])
        asyncio.run(scenario())

    def test_changed_packet_cannot_be_sent(self):
        async def scenario():
            plugin=self.make_plugin(mode='auto')
            await plugin._maybe_daily_news(at(9))
            checksum=self.row(plugin)['metadata']['approval']['digest']
            path=plugin._news_approval_dir()/(checksum+'.json')
            data=json.loads(path.read_text());data['draft']['title']='changed'
            path.write_text(json.dumps(data))
            await self.approve(plugin)
            self.assertEqual(self.client.posts,[]);self.assertEqual(self.client.uploads,[])
        asyncio.run(scenario())

    def test_image_must_reach_owner_before_approval_is_accepted(self):
        async def scenario():
            plugin=self.make_plugin(mode='auto')
            self.send.image=mock.AsyncMock(return_value=False)
            await plugin._maybe_daily_news(at(9))
            self.assertFalse(self.row(plugin)['metadata']['approval']['delivered_at'])
            await self.approve(plugin)
            self.assertEqual(self.client.posts,[])
            self.send.image.return_value=True
            await plugin._maybe_daily_news(at(9,11))
            await self.approve(plugin,at(9,12))
            self.assertEqual(len(self.client.posts),1)
        asyncio.run(scenario())

    def test_failed_upload_does_not_downgrade_approved_image_to_text(self):
        async def scenario():
            plugin=self.make_plugin(mode='auto')
            await plugin._maybe_daily_news(at(9))
            self.client.upload_image_bytes=mock.AsyncMock(side_effect=RuntimeError('upload failed'))
            await self.approve(plugin)
            self.assertEqual(self.client.posts,[])
            self.assertEqual(self.row(plugin)['status'],OutgoingStatus.AWAITING_APPROVAL.value)
            self.assertFalse(self.row(plugin)['metadata']['approval']['approved_at'])
        asyncio.run(scenario())

    def test_mode_change_and_profile_change_block_pending_send(self):
        async def scenario():
            plugin=self.make_plugin(mode='auto')
            await plugin._maybe_daily_news(at(9))
            plugin.config.news.mode='dry_run'
            await self.approve(plugin)
            self.assertEqual(self.client.posts,[])
            plugin.config.news.mode='auto';plugin.config.basic.profile_id='other'
            await self.approve(plugin)
            self.assertEqual(self.client.posts,[])
        asyncio.run(scenario())

    def test_verified_but_unapproved_draft_is_blocked_at_publish_boundary(self):
        async def scenario():
            plugin=self.make_plugin(mode='auto')
            await plugin._maybe_daily_news(at(9))
            checksum=self.row(plugin)['metadata']['approval']['digest']
            draft=news_approval.restore(news_approval.load(plugin._news_approval_dir(),checksum))
            with mock.patch.object(PLUGIN_MODULE['time'],'time',return_value=at(9,5)):
                with self.assertRaises(PLUGIN_MODULE['daily_news'].NewsRetry):
                    await plugin._publish_news('news:2026-11-06',draft,self.client,'201')
            self.assertEqual(self.client.posts,[]);self.assertEqual(self.client.uploads,[])
        asyncio.run(scenario())

    def test_message_before_the_preview_does_not_approve_new_draft(self):
        async def scenario():
            plugin=self.make_plugin(mode='auto')
            with mock.patch.object(PLUGIN_MODULE['time'],'time',return_value=at(9)):
                await plugin._maybe_daily_news(at(9))
            with mock.patch.object(PLUGIN_MODULE['time'],'time',return_value=at(9,5)):
                await plugin._confirm_news_approval(message_at=at(8,59))
            self.assertEqual(self.client.posts,[])
        asyncio.run(scenario())

    def test_sdk_message_object_identity_is_supported(self):
        msg=SimpleNamespace(platform='qq',processed_plain_text='可以发送',message_info=SimpleNamespace(
            user_info=SimpleNamespace(user_id='owner'),group_info=None,time=100))
        self.assertEqual(news_approval.message_identity(msg),('qq','owner','','可以发送',100))
