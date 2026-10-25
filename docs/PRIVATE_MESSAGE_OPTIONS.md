# Direct-message options

The current release does not include automatic direct-message sending. The server keeps this capability disabled because the web client exposes reading but no stable reusable send contract.

Possible future approaches are recorded in the project-level Chinese document docs/plans/小黑盒私信方案记录.md: a physical Android phone with a local agent, a local emulator, a GPU cloud emulator, a cloud phone, or protocol analysis. The physical Android phone with a local agent is the preferred first prototype.

Any future implementation should start in manual-confirmation mode, use an allowlist and daily quota, verify the sent result by rereading the conversation or taking a screenshot, and never retry an unknown result automatically.
