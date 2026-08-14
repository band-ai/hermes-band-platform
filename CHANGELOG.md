# Changelog

## [0.2.0](https://github.com/band-ai/hermes-band-platform/compare/v0.1.0...v0.2.0) (2026-08-14)


### Features

* **adapter:** declare code-block and native-chunking capabilities ([fcc4708](https://github.com/band-ai/hermes-band-platform/commit/fcc470849a6ebde8aa70420950b3d9e27503ea8f))
* **adapter:** declare code-block and native-chunking capabilities ([ac16d93](https://github.com/band-ai/hermes-band-platform/commit/ac16d931ed3186e328f354d1227ae564a6437174))
* **adapter:** emit error events when a turn fails ([1c1ede7](https://github.com/band-ai/hermes-band-platform/commit/1c1ede7e96b7bbba2b526b92d984e2c98b10c9b0))
* **adapter:** emit error events when a turn fails ([45674bc](https://github.com/band-ai/hermes-band-platform/commit/45674bc5f54e3b6925c037fe3b68ab88d531a96f))
* **adapter:** emit per-turn usage events ([1edecd4](https://github.com/band-ai/hermes-band-platform/commit/1edecd4af3fb564283b83ad35d722ef625204e15))
* **adapter:** emit per-turn usage events ([f063c8c](https://github.com/band-ai/hermes-band-platform/commit/f063c8c0771faa4bba15b0da5340a2ca8149a181))
* **adapter:** emit tool-call and tool-result execution events ([d5189ba](https://github.com/band-ai/hermes-band-platform/commit/d5189baecbe344adb2a29861427776f31c1bba6a))
* **adapter:** emit tool-call and tool-result execution events ([a509164](https://github.com/band-ai/hermes-band-platform/commit/a509164799f38719ffa0beb0ad927012f77a9bb6))
* **adapter:** gate execution events behind BAND_EMIT_EXECUTION ([8ad389b](https://github.com/band-ai/hermes-band-platform/commit/8ad389b9c5db55867ba7c8074bb9f55e97e42343))
* **adapter:** log send-path outcomes and failures ([ff831b3](https://github.com/band-ai/hermes-band-platform/commit/ff831b3574c4bc8d5bf6a5256ee39e5fe3c9d19f))
* **adapter:** log working-indicator outcomes ([f6b5966](https://github.com/band-ai/hermes-band-platform/commit/f6b59665ee44854e1cbce56350ebd9907d28bd00))
* **adapter:** register a standalone_sender_fn for out-of-process delivery ([f68b9e3](https://github.com/band-ai/hermes-band-platform/commit/f68b9e388822ec95599eca8a3a91601274425d31))
* **adapter:** register a standalone_sender_fn for out-of-process delivery ([c35c56a](https://github.com/band-ai/hermes-band-platform/commit/c35c56a618809f3091f71b81fff1cac1ddc52261))
* **adapter:** report working state to Band via the activity API ([391f35c](https://github.com/band-ai/hermes-band-platform/commit/391f35cf1eb7dfc0243d50417aaa7356ac31ba93))
* **adapter:** report working state to Band via the activity API ([77fa886](https://github.com/band-ai/hermes-band-platform/commit/77fa8863a95825a3b849e93b4fe6718d5e2ba7a1))
* **error_events:** log emission outcomes and failures ([e08b460](https://github.com/band-ai/hermes-band-platform/commit/e08b4603094e43b6c98fb00c3a39234c3bd959ed))
* **events:** gate execution events behind BAND_EMIT_EXECUTION ([0d6645d](https://github.com/band-ai/hermes-band-platform/commit/0d6645de83028f10a3c97ad9821270af2e002d9f))
* **execution_events:** log emission outcomes and failures ([ce1bfe5](https://github.com/band-ai/hermes-band-platform/commit/ce1bfe5d175e424f28749a8208a385a2298eebe0))
* **usage_events:** log emission outcomes and failures ([30334a8](https://github.com/band-ai/hermes-band-platform/commit/30334a8fef3a48fd5419338947dde8aa32d1f6c8))


### Bug Fixes

* **adapter:** bound and drain in-flight execution-event submissions ([d78c3d9](https://github.com/band-ai/hermes-band-platform/commit/d78c3d98a8ff812d2f96c327be34d443ef853edd))
* **adapter:** bound and drain in-flight execution-event submissions ([d970e18](https://github.com/band-ai/hermes-band-platform/commit/d970e1850dd2ff52cf08c3939c0c24ea6d9c75f3))
* **adapter:** bound execution-event payloads instead of cutting their JSON ([34b5fe4](https://github.com/band-ai/hermes-band-platform/commit/34b5fe4ec9de0b64e725e1bb81df566035f0340a))
* **adapter:** close the standalone sender's HTTP client ([1ceea76](https://github.com/band-ai/hermes-band-platform/commit/1ceea76fec05f3081611540c567bc2b19e0ceba9))
* **adapter:** close the standalone sender's HTTP client ([e1bf6b6](https://github.com/band-ai/hermes-band-platform/commit/e1bf6b65a407e84665a45d12e7508602b63dca19))
* **adapter:** default usage emission to off until a reader exists ([4b024d2](https://github.com/band-ai/hermes-band-platform/commit/4b024d24f8011963ef2f8b6582a3b9ac31e8fc6f))
* **adapter:** emit usage for turns that never complete ([80ab7c2](https://github.com/band-ai/hermes-band-platform/commit/80ab7c26dbb92b0b3ae30f6b41639d41d5d0f57d))
* **adapter:** forget working-indicator state when the link or room goes away ([333f28a](https://github.com/band-ai/hermes-band-platform/commit/333f28aceae3d871466087d4d7a5508c01a3d91b))
* **adapter:** forget working-indicator state when the link or room goes away ([89b0c14](https://github.com/band-ai/hermes-band-platform/commit/89b0c14eabd73ed3c12f1fcfb37a64ea997244cf))
* **adapter:** stop double-posting by correcting the platform_hint ([b6e4749](https://github.com/band-ai/hermes-band-platform/commit/b6e47497016b102d677646f1bc7eeb5305fb9f1a))
* **adapter:** stop double-posting by correcting the platform_hint ([a3bdffa](https://github.com/band-ai/hermes-band-platform/commit/a3bdffa349755bea1a0dbc2206abc4d5e60d6bd5))
* **events:** bound execution-event payloads field-by-field, not by cutting JSON ([14ccebb](https://github.com/band-ai/hermes-band-platform/commit/14ccebb738acefb497e88cd0303510ecfaaf2035))
* **events:** isolate delivery failures by room ([a1a5669](https://github.com/band-ai/hermes-band-platform/commit/a1a56696c2b36268824db05ca05e829b1cb55e5a))
* **skill:** align Band reply guidance ([611a31b](https://github.com/band-ai/hermes-band-platform/commit/611a31bf7cdd7ee0b2f09b288bb1314287e45ce7))
* **tools:** decline the live link's REST client on a cross-loop call ([a97177d](https://github.com/band-ai/hermes-band-platform/commit/a97177dd44b9f3952586d021a7a13b2dfedd4779))
* **tools:** decline the live link's REST client on a cross-loop call ([314dc70](https://github.com/band-ai/hermes-band-platform/commit/314dc7046d142f07200071b0e6fd2af5813535b3))
* **usage:** emit usage for turns that never complete ([8a97ee9](https://github.com/band-ai/hermes-band-platform/commit/8a97ee982abbefea0a0e4e35f4b614e894c95276))


### Performance Improvements

* **adapter:** bound the failure reason before redacting it ([3b95695](https://github.com/band-ai/hermes-band-platform/commit/3b95695cc3765a8e624398517fd2b8209ed647f4))

## [0.1.0](https://github.com/band-ai/hermes-band-platform/compare/v0.0.1...v0.1.0) (2026-07-28)


### Features

* Band (Thenvoi) platform plugin for Hermes Agent v1.0.0 ([a367f0e](https://github.com/band-ai/hermes-band-platform/commit/a367f0ee4a864c66481ece14f48eb9da0caa4381))
* package Band setup flow ([3b18662](https://github.com/band-ai/hermes-band-platform/commit/3b1866291a93ba96adc8f4109833a0bcc0f33ba6))
* **packaging:** publish hermes-band from a rebuilt release flow ([#20](https://github.com/band-ai/hermes-band-platform/issues/20)) ([18d0fbc](https://github.com/band-ai/hermes-band-platform/commit/18d0fbcda709e8f4db74a811d180b0f31040bd2c))
* **packaging:** ship band as a HERMES_HOME directory plugin (read-only gateway venv fix) ([#13](https://github.com/band-ai/hermes-band-platform/issues/13)) ([5693bb8](https://github.com/band-ai/hermes-band-platform/commit/5693bb8bdbdffad74577a3aa58962f7a29b12d1c))


### Bug Fixes

* **adapter:** accept is_reconnect kwarg in BandAdapter.connect ([150ee43](https://github.com/band-ai/hermes-band-platform/commit/150ee430cc90386b9f3e41447c0177080a636255))
* **adapter:** accept is_reconnect kwarg in BandAdapter.connect ([7d4452a](https://github.com/band-ai/hermes-band-platform/commit/7d4452a79f6a1f4be7db718c894fbc06461cb5fc))
* **adapter:** Band-managed durable history rehydration (INT-910) ([cf1220a](https://github.com/band-ai/hermes-band-platform/commit/cf1220ae00f4560e782fb5c593a8941115cfb6c6))
* **adapter:** cancel per-room re-join drains on disconnect ([b4b5840](https://github.com/band-ai/hermes-band-platform/commit/b4b5840c78c083f8c004427e6adc892b30a67d77))
* **adapter:** close re-join drain gap, transcript reorder, silent seed failure ([4168846](https://github.com/band-ai/hermes-band-platform/commit/4168846b742ed8c58da7dc827ea8118c4b475d63))
* **adapter:** durably seed Band history into the session transcript (INT-910) ([bfe3443](https://github.com/band-ai/hermes-band-platform/commit/bfe34432a53fb607c6aca0735552af20a07d681a))
* **adapter:** harden cold-room seed against the cross-thread race (INT-910) ([8ab9102](https://github.com/band-ai/hermes-band-platform/commit/8ab9102d2212b519cc23e08041eab530aec7cfc5))
* **adapter:** harden durable rehydration after code review (INT-910) ([d626a40](https://github.com/band-ai/hermes-band-platform/commit/d626a402abfb5b89c163ead87a7e913ba2bbc5a9))
* **adapter:** marshal cross-loop sends back onto the link's event loop ([4dabb5d](https://github.com/band-ai/hermes-band-platform/commit/4dabb5da7b4e6d73b75a9f1d091f5ba4bb47c000))
* **adapter:** marshal cross-loop sends back onto the link's event loop (INT-899) ([aa2155d](https://github.com/band-ai/hermes-band-platform/commit/aa2155d9e9892a8ddaef38eb945cb9ccd40f2673))
* **adapter:** re-check link in _send_on_link to close disconnect race ([1d11e71](https://github.com/band-ai/hermes-band-platform/commit/1d11e713daaccc4e1b9c6a52c71d1ac0a09ffd40))
* **adapter:** seed empty-check counts active rows; test history-based warmth ([f1d8d1b](https://github.com/band-ai/hermes-band-platform/commit/f1d8d1b770bce9baf3eff669dd9d8e18a131c97e))
* **ci:** fail fast when GitHub App token secrets are missing ([#15](https://github.com/band-ai/hermes-band-platform/issues/15)) ([c78d143](https://github.com/band-ai/hermes-band-platform/commit/c78d143bfa7c219e2e409852570640d08bb0007e))
* harden message drain + room tracking, green the unit suite ([ee292bf](https://github.com/band-ai/hermes-band-platform/commit/ee292bf98718d20bb622d17403280bba93b8ee1a))
* keep the Band user key out of the LLM during add-band setup ([cc8cdd0](https://github.com/band-ai/hermes-band-platform/commit/cc8cdd0a4ee5449e80fb39bff5f1edf9ed1c3a54))
* **packaging:** annotate __version__ so release-please bumps it ([#24](https://github.com/band-ai/hermes-band-platform/issues/24)) ([76e6c24](https://github.com/band-ai/hermes-band-platform/commit/76e6c24d9c417de0cb2243776bf119b90fe8e6ef))
* review pass — mask API key, align SDK pin, smooth onboarding ([231fb82](https://github.com/band-ai/hermes-band-platform/commit/231fb8291cab7307ce0641b4e0ad30f9b965dbd8))
* **skill:** make verify_roundtrip.py runnable on a directory-plugin install ([#17](https://github.com/band-ai/hermes-band-platform/issues/17)) ([84045db](https://github.com/band-ai/hermes-band-platform/commit/84045db506844417eeaecf8f471085d855660a43))
* **skill:** stop the gateway-Python resolver blessing a non-gateway interpreter ([#16](https://github.com/band-ai/hermes-band-platform/issues/16)) ([25b95bf](https://github.com/band-ai/hermes-band-platform/commit/25b95bf6140b6a4f89c3af48fcccd0449a4f3b00))
* track root plugin.yaml version with release-please and guard drift ([df8062d](https://github.com/band-ai/hermes-band-platform/commit/df8062da5178879f8ac61b38a77f135e5b1ef7ae))


### Documentation

* **adapter:** note residual seed-if-empty TOCTOU for later (INT-910) ([98239b6](https://github.com/band-ai/hermes-band-platform/commit/98239b69f1258f06a84150907bfd8d1516c5fdf5))
* add as-shipped write-mechanism note to rehydration-design (INT-910) ([b081747](https://github.com/band-ai/hermes-band-platform/commit/b0817476da7da29724e81cac0dc482b49c51931f))
* add Band install flow and harden the add-band skill ([af8b384](https://github.com/band-ai/hermes-band-platform/commit/af8b384d701d97de98fc642ad3100e7b2c4d7e21))
* add Band-managed history rehydration design plan (INT-910) ([8aaf1f5](https://github.com/band-ai/hermes-band-platform/commit/8aaf1f5e76fddbb9d123f05862881172842c6005))
* align README, comments, and design doc with durable rehydration (INT-910) ([03c64a5](https://github.com/band-ai/hermes-band-platform/commit/03c64a5b4cb98ae24e8ccd23c64810c5449d9890))
* Band install flow, add-band skill security, and manifest guard ([fc92fd3](https://github.com/band-ai/hermes-band-platform/commit/fc92fd3c5f3097b8f516ded70267a8e9af07ed79))
* consolidate rehydration design into a single doc ([81b3165](https://github.com/band-ai/hermes-band-platform/commit/81b3165524955a7fd8058c93e04db782c4c32bc8))
* detailed design + retrospective for the seed-if-empty race (INT-910) ([5f08b21](https://github.com/band-ai/hermes-band-platform/commit/5f08b21591fae5f78a99abc07ed11892e3d97dab))
* lead with directory-install path; honest pip enable note ([87517ee](https://github.com/band-ai/hermes-band-platform/commit/87517ee531285fc964c4fba19bb3e1fd0c564a16))
* mark superseded race-design sections (lock removed; atomic shipped) (INT-910) ([74ad932](https://github.com/band-ai/hermes-band-platform/commit/74ad932b2574ea14fa139f6cbf5be0c26b098eae))
* point install path at the Band web app / add-band bootstrapper ([16b06ef](https://github.com/band-ai/hermes-band-platform/commit/16b06ef3537464cbcd289009005f768103e637c0))
* secure manual install flow; fix stale add-band reference ([a42efbe](https://github.com/band-ai/hermes-band-platform/commit/a42efbe6ccf8ca6ff96b2fa4ed5c5f3b138a7a1f))
