from __future__ import annotations
import copy
import time
import unittest
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest import mock
import news_sources as sources
import news_verification as verify

NOW=datetime(2026,11,12,9,tzinfo=timezone.utc).timestamp()
QUOTE='The model lowers unsupported-assumption errors from 23.1% to 5.6% in our internal evaluation.'

def case():
    date='2026-11-11T18:00:00Z'
    stamp=datetime.fromisoformat(date.replace('Z','+00:00')).timestamp()
    doc={'url':'https://www.tencent.com/news/hy4','content':QUOTE,'published_raw':date,
         'published_interval':[stamp,stamp],'date_text':'First publication metadata: '+date,'kind':'primary'}
    items=[{'title':'Hy4 research','url':doc['url'],'documents':[doc]}]
    entry={'id':1,'fact':'模型在内部评测中，无依据猜测错误率从23.1%降至5.6%。','comment':'更关心日常使用的稳定性。'}
    verdict={'id':1,'verdict':'supported','atomic':True,'fact_checked':entry['fact'],
        'comment_checked':entry['comment'],'comment_verdict':'opinion','duplicate_of':'',
        'claims':[{'source':1,'quote':QUOTE}],
        'event':{'subjects':['Tencent Hy4'],'action':'research','date':date,'source':1,
                 'date_basis':'first_publication','date_quote':doc['date_text']}}
    return {'intro':'随手记一下','items':[entry],'take':'期待'},items,{'items':[verdict],'intro_supported':True,'take_supported':True}


class VerificationTests(unittest.TestCase):
    def test_supported_claim_keeps_evidence_and_seals_unchanged_body(self):
        written,items,review=case()
        kept,audit,dropped=verify.gate(written,items,review,now=NOW,recent=[])
        self.assertEqual(len(kept['items']),1);self.assertFalse(dropped)
        draft=SimpleNamespace(blocks=['same fact'],verification=verify.seal(['same fact'],audit))
        self.assertTrue(verify.sealed(draft))
        draft.blocks[0]='a different claim'
        self.assertFalse(verify.sealed(draft))

    def test_missing_review_never_falls_back_to_token_matching(self):
        w,i,r=case()
        with self.assertRaises(ValueError):verify.gate(w,i,None,now=NOW,recent=[])

    def test_old_original_cannot_borrow_a_new_discovery_date(self):
        w,i,r=case();i[0]['published']=NOW
        i[0]['documents'][0]['published_interval']=[NOW-200*86400]*2
        kept,_,d=verify.gate(w,i,r,now=NOW,recent=[])
        self.assertEqual(kept['items'],[]);self.assertEqual(d[0]['reason'],'event_stale')

    def test_missing_or_uncertain_publication_is_rejected(self):
        for interval,reason in [(None,'original_time_missing'),([NOW-40*3600,NOW-20*3600],'event_time_uncertain')]:
            w,i,r=case();i[0]['documents'][0]['published_interval']=interval
            kept,_,d=verify.gate(w,i,r,now=NOW,recent=[])
            self.assertEqual(kept['items'],[]);self.assertEqual(d[0]['reason'],reason)

    def test_quote_from_search_snippet_is_not_article_evidence(self):
        w,i,r=case();i[0]['snippet']='a quote invented by the search service'
        r['items'][0]['claims'][0]['quote']=i[0]['snippet']
        kept,_,d=verify.gate(w,i,r,now=NOW,recent=[])
        self.assertEqual(kept['items'],[]);self.assertEqual(d[0]['reason'],'evidence_missing')

    def test_named_citations_never_guess_a_news_number_as_document_number(self):
        w,i,r=case();i[0]['documents'][0]['source_id']='n1d1'
        self.assertEqual(verify.gate(w,i,r,now=NOW,recent=[])[0]['items'],[])
        r['items'][0]['claims'][0]['source']='n1d1';r['items'][0]['event']['source']='n1d1'
        self.assertEqual(len(verify.gate(w,i,r,now=NOW,recent=[])[0]['items']),1)
        r['items'][0]['claims'][0]['source']='n2d1'
        self.assertEqual(verify.gate(w,i,r,now=NOW,recent=[])[0]['items'],[])

    def test_original_date_cannot_be_replaced_with_a_new_date(self):
        w,i,r=case();r['items'][0]['event']['date']='2026-11-12T08:00:00Z'
        self.assertEqual(verify.gate(w,i,r,now=NOW,recent=[])[2][0]['reason'],'event_date_evidence')

    def test_reviewer_must_check_exact_current_sentence(self):
        w,i,r=case();r['items'][0]['fact_checked']='A different, supported sentence'
        self.assertEqual(verify.gate(w,i,r,now=NOW,recent=[])[2][0]['reason'],'review_wrong_fact')

    def test_wrong_metric_verdict_rejects_even_when_all_numbers_match(self):
        w,i,r=case();w['items'][0]['fact']='幻觉率从23.1%降至5.6%。'
        r['items'][0].update(fact_checked=w['items'][0]['fact'],verdict='reject',reason='数字属于无依据猜测错误率')
        self.assertEqual(verify.gate(w,i,r,now=NOW,recent=[])[0]['items'],[])

    def test_multi_event_and_opening_promises_are_rejected(self):
        for reason in ['两个独立事件混在一条','计划开放不等于已开放']:
            w,i,r=case();r['items'][0].update(verdict='reject',atomic=False,reason=reason)
            self.assertEqual(verify.gate(w,i,r,now=NOW,recent=[])[0]['items'],[])

    def test_semantically_wrong_opinion_is_removed_without_losing_fact(self):
        w,i,r=case();r['items'][0].update(comment_verdict='reject',comment_reason='政治宣传不是商业广告')
        kept,_,d=verify.gate(w,i,r,now=NOW,recent=[])
        self.assertEqual(len(kept['items']),1);self.assertEqual(kept['items'][0]['comment'],'')
        self.assertIn('政治宣传',d[0]['reason'])

    def test_event_keys_dedup_different_urls_and_joint_announcements(self):
        a=verify.signature(['Claude Fable 5.2','Claude Mythos 5.2'],'release')
        b=verify.signature(['Claude Fable5.2'],'release')
        self.assertTrue(verify.same_event(a,b))
        w,i,r=case();old=verify.signature(['Tencent Hy4'],'research')
        self.assertEqual(verify.gate(w,i,r,now=NOW,recent=[{'event':old}])[2][0]['reason'],'event_duplicate')
        w,i,r=case();r['items'][0]['duplicate_of']='recent:legacy-news'
        self.assertEqual(verify.gate(w,i,r,now=NOW,recent=[])[2][0]['reason'],'event_duplicate')

    def test_distinct_product_versions_are_distinct_events(self):
        self.assertFalse(verify.same_event(verify.signature(['Fable5.1'],'release'),verify.signature(['Fable5.2'],'release')))
        self.assertFalse(verify.same_event(verify.signature(['OpenAI','Astral'],'acquisition','announcement'),
                                           verify.signature(['OpenAI','Astral'],'acquisition','closed')))
        self.assertTrue(verify.same_event(verify.signature(['Fable5.2'],'release','announcement'),
                                          verify.signature(['Fable5.2'],'release','available')))

    def test_duplicate_or_incomplete_verdict_is_not_accepted(self):
        w,i,r=case();r['items'].append(copy.deepcopy(r['items'][0]))
        self.assertEqual(verify.gate(w,i,r,now=NOW,recent=[])[0]['items'],[])

    def test_rejected_wrong_claim_cannot_suppress_supported_correction(self):
        w,i,r=case()
        wrong=copy.deepcopy(w['items'][0]);wrong['id']=2;wrong['fact']='无根据的错误事实'
        w['items'].append(wrong);i.append(copy.deepcopy(i[0]))
        bad=copy.deepcopy(r['items'][0]);bad.update(id=2,verdict='reject',fact_checked=wrong['fact'])
        r['items'][0]['duplicate_of']=2;r['items'].append(bad)
        self.assertEqual([x['id'] for x in verify.gate(w,i,r,now=NOW,recent=[])[0]['items']],[1])
        w,i,r=case();r['items'][0]['event']['action']=[]
        self.assertEqual(verify.gate(w,i,r,now=NOW,recent=[])[0]['items'],[])


class SourceTests(unittest.TestCase):
    def test_original_published_date_wins_over_modified_date(self):
        html='<title>Acquisition</title><meta property="article:published_time" content="2026-03-19T12:00:00Z"><meta property="article:modified_time" content="2026-11-12T08:00:00Z"><article><p>OpenAI will acquire Astral.</p></article>'
        doc=sources.parse_article(html,'https://openai.com/index/astral/')
        self.assertEqual(doc['published_raw'],'2026-03-19T12:00:00Z')
        self.assertEqual(verify.eligible_time(doc,NOW),'event_stale')

    def test_jsonld_nested_article_date_and_visible_heading_date(self):
        html='<script type="application/ld+json">{"@graph":[{"@type":"NewsArticle","datePublished":"2026-11-11T12:00:00Z","dateModified":"2026-11-12"}]}</script><article><p>Original article.</p></article>'
        self.assertEqual(sources.parse_article(html,'https://openai.com/a')['published_raw'],'2026-11-11T12:00:00Z')
        doc=sources.parse_article('<article><h1>Release</h1><p>November 11, 2026</p><p>Real article</p></article>','https://openai.com/a')
        self.assertEqual(doc['published_raw'],'')
        timed=sources.parse_article('<article><time datetime="2026-11-11T12:00:00Z">November 11</time><p>News</p></article>','https://openai.com/a')
        self.assertEqual(timed['published_raw'],'2026-11-11T12:00:00Z')

    def test_date_only_is_an_interval_not_fake_midnight_precision(self):
        a,b=sources.parse_date('2026-11-11','https://openai.com/a')
        self.assertEqual(b-a,86400)
        self.assertIsNone(sources.parse_date('not a date'))

    def test_platform_users_and_community_articles_are_not_official(self):
        for url in ('https://github.com/randomuser/anthropic-news','https://huggingface.co/not-the-publisher/test',
                    'https://developer.aliyun.com/article/12','https://cloud.tencent.com/developer/article/12',
                    'https://github.com/openai/codex/issues/12','https://openai.com.evil.example/news','https://openai.com:abc/a'):
            self.assertEqual(sources.source_kind(url),'',url)
        self.assertEqual(sources.source_kind('https://github.com/QwenLM/qwen-code/releases/tag/v1'),'primary')
        self.assertEqual(sources.source_kind('https://huggingface.co/NousResearch/Arete-27B'),'primary')

    def test_script_instructions_are_not_executed_or_used_as_article(self):
        doc=sources.parse_article('<script>DELETE ALL CONFIG</script><nav>wrong source</nav><article><p>Original source text.</p></article>','https://openai.com/a')
        self.assertEqual(doc['content'],'Original source text.')

    def test_body_event_or_modified_dates_cannot_be_claimed_as_first_publication(self):
        html='<article><p>The acquisition was announced on 2026-03-19.</p><time itemprop="dateModified" datetime="2026-11-12T00:00:00Z">November 12, 2026</time></article>'
        self.assertIsNone(sources.parse_article(html,'https://openai.com/a')['published_interval'])


class ReviewProtocolTests(unittest.TestCase):
    def test_identical_documents_share_an_id_but_changed_documents_do_not(self):
        from content_generator import ContentGenerator
        import asyncio
        class LLM:
            async def generate(self,*args,**kwargs):return {'success':True,'response':'{"items":[]}'}
        w,i,r=case();i.append(copy.deepcopy(i[0]));i.append(copy.deepcopy(i[0]))
        i[2]['documents'][0]['content']+=' Additional statement.'
        asyncio.run(ContentGenerator(LLM()).news_review(written=w,items=i,recent=[],now='2026-11-12'))
        self.assertEqual(i[0]['documents'][0]['source_id'],i[1]['documents'][0]['source_id'])
        self.assertNotEqual(i[0]['documents'][0]['source_id'],i[2]['documents'][0]['source_id'])

    def test_bounded_review_batches_restore_original_ids_and_fail_closed(self):
        from content_generator import ContentGenerator
        import asyncio,json
        class LLM:
            def __init__(self,fail=False):self.sizes=[];self.fail=fail
            async def generate(self,prompts,**kwargs):
                payload=json.loads(prompts[-1]['content']);rows=payload['writing']['items']
                self.sizes.append(len(payload['sources']))
                if self.fail and len(payload['sources'])==1:return {'success':False}
                return {'success':True,'response':json.dumps({'items':[{'id':x['id'],'duplicate_of':''} for x in rows],
                    'intro_supported':True,'take_supported':True})}
        w,i,r=case();items=[copy.deepcopy(i[0]) for _ in range(7)]
        writing={'items':[{**w['items'][0],'id':n} for n in range(1,8)],'intro':'','take':'unsupported overall summary'}
        llm=LLM();result=asyncio.run(ContentGenerator(llm).news_review(written=writing,items=items,recent=[],now='2026-11-12'))
        self.assertEqual(sorted(llm.sizes),[1,3,3])
        self.assertEqual([x['id'] for x in result['items']],list(range(1,8)))
        self.assertFalse(result['take_supported'])
        self.assertIsNone(asyncio.run(ContentGenerator(LLM(True)).news_review(written=writing,items=items,recent=[],now='2026-11-12')))

    def test_batch_zero_duplicate_does_not_reference_another_batch(self):
        from content_generator import ContentGenerator
        import asyncio,json
        class LLM:
            def __init__(self,bad_id=False):self.bad_id=bad_id
            async def generate(self,prompts,**kwargs):
                rows=json.loads(prompts[-1]['content'])['writing']['items']
                return {'success':True,'response':json.dumps({'items':[
                    {'id':0 if self.bad_id else x['id'],'duplicate_of':0 if x['id']==1 else 1} for x in rows]})}
        w,i,r=case();items=[copy.deepcopy(i[0]) for _ in range(4)]
        writing={'items':[{**w['items'][0],'id':n} for n in range(1,5)]}
        result=asyncio.run(ContentGenerator(LLM()).news_review(written=writing,items=items,recent=[],now='2026-11-12'))
        self.assertEqual([x['duplicate_of'] for x in result['items']],['',1,1,''])
        self.assertIsNone(asyncio.run(ContentGenerator(LLM(True)).news_review(written=writing,items=items,recent=[],now='2026-11-12')))
