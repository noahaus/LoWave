# Generator browser regression check

Run from the repository root after installing the existing Python and Node dependencies and Google Chrome:

```sh
node verification/verify-generated.cjs
```

This runs four synthetic assertion cases through the generator at baseline commit `00c8918a8f544b3993ebb28005b328ec0034ece0` and the current checkout, then executes each assertion against correct and deliberately incorrect HTML in Chrome. The baseline commit must be available in local Git history. It does not fetch history automatically.

The cases cover an exact field value, an empty field, checked consent, and authored text conflicting with substituted model text. Each case creates a fresh disposable page. No LLM calls, API credentials, server, or real application data are used. The generated code comes only from the supplied synthetic cases and local repository code.

The process fails if the current generator rejects a positive control or accepts a negative control. Baseline outcomes are printed for comparison, not counted as current failures. The checks establish these assertion behaviors, not full application coverage or model correctness. This is an opt-in browser check, separate from fast Python tests, because it requires Chrome and repository history.

Incomplete generation (TODO / empty / low-confidence required steps) is a second opt-in Chrome check:

```sh
node verification/verify-incomplete.cjs
```

A complete local `data:` page must pass. An incomplete spec must fail before navigation, including when exported with `--allow-incomplete`. No target application is contacted.
