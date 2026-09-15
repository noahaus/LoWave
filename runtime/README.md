# Runtime authentication boundary

Runtime authentication is an explicit, local-first opt-in for trusted hook code. The hook is ordinary user-owned JavaScript with the same authority as the QA process; it is not sandboxed. Configure it only through `runPipeline({ authHook: "/absolute/path/to/hook.cjs" })`. There is no GUI picker yet.

The hook exports `authenticate({ page, baseURL, origin })`. It resolves or creates credentials inside the approved runtime and must not print them. Credentials never enter parser prompts, plans, generated specs, command arguments, or pipeline logs. Browser state is transferred through bounded local pipes and remains in memory.

This first slice deliberately accepts state only for the exact canonical application origin and cookies applicable to its hostname. Federated or cross-origin authentication state is rejected rather than filtered. A generated authenticated spec also fails closed when `QA_AUTH_HOOK` is absent.
