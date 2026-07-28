# Changelog

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
