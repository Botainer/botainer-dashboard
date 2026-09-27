# Pydantic Core component attribution

This supplement accompanies the separately distributed, unmodified Pydantic Core
2.46.5 dependency wheel identified below. It preserves notices for the 103 Cargo
components in that wheel's embedded CycloneDX inventory. The inventory declares
an all-target scope: this conservative collection includes target-specific and
build dependencies and does not assert that every listed component is compiled
into this particular native binary. It does not attest to a reproducible build
or cover different versions, platforms or wheel artifacts.

The component map records the crate archive hashes supplied by that inventory.
The source notices were collected from the corresponding versioned source pages,
with an immutable source-commit reference where indicated. The archive hashes
are identifiers from the upstream inventory, not claims that this distribution
obtained or rebuilt those crate archives. HTML source-view captures were reduced
to their source text; the text hashes below identify the retained notice bytes.
The raw inventory and source-view HTML are not copied into this supplement.

## Reviewed artifact

| Item | Identity | SHA-256 |
| --- | --- | --- |
| Dependency wheel | `pydantic_core-2.46.5-cp313-cp313-macosx_11_0_arm64.whl` | `f332f0e72a5a0400141f830744e141bf9f97917878dbe968669e8a7fefea78ff` |
| Native wheel member | `pydantic_core/_pydantic_core.cpython-313-darwin.so` | `e040625fdb57a9edb69c9a2351703c72061a9888d8ad8e874d696ba448c83c7f` |
| Embedded inventory member | `pydantic_core-2.46.5.dist-info/sboms/pydantic-core.cyclonedx.json` | `82ae839c408ef85848e7144d8c2b2e309bc91db15a730b992b11653a4327955d` |

Pydantic Core's own MIT license remains in the wheel at
`pydantic_core-2.46.5.dist-info/licenses/LICENSE`. Preserve the original wheel and
its notices. These supplements do not replace that license, modify the wheel,
or change the dashboard application's license.

## Distribution choices and additional portions

For components offering MIT as an alternative, this distribution selects MIT.
Other supplied alternative-license texts are retained for context; their inclusion
does not make mutually alternative licenses cumulative. The per-component entries
preserve the upstream expression and state the selection separately.

Additional terms and attribution remain applicable to their identified portions:

- `unicode-ident` requires **Unicode-DFS-2016 in addition to MIT**. Its historical
  Unicode notice is preserved; it is not replaced by a newer generic notice.
- The ICU4X-related `Unicode-3.0` components retain their full supplied license,
  including the additional IBM attribution for possible ICU4C/ICU4J adaptations.
- `target-lexicon` supplies **Apache-2.0 WITH LLVM-exception**; its complete
  exception is retained with the Apache text.
- `foldhash` supplies **Zlib** terms, including origin and altered-source notices.
- The lexical `LICENSE.md` retains feature-dependent Go/V8 BSD, fpconv MIT,
  Boost and Apache-with-LLVM material. Selecting MIT for lexical's own code does
  not replace those portions' terms. The complete upstream explanation is
  preserved without claiming all described features were compiled into this wheel.
- The `utf16_iter`, `utf8_iter` and `write16` copyright files retain the additional
  provenance and scope distinctions supplied by their authors.
- For `r-efi`, MIT is selected. Its complete `AUTHORS` file is retained, including
  its explanations of the unselected Apache/LGPL alternatives and author credits.

[The accompanying notice collection](pydantic-core-NOTICES.txt) contains all 178
mapped notices as 58 byte-distinct texts. Exact duplicates share a SHA-256 entry;
no attribution is dropped merely because multiple components use the same text.
Its framing is outside the original notice bytes, which preserve original line
endings and whether the final line has a newline.

Rust toolchain/standard-library material is separate from this Cargo inventory.
Keep the [Rust attribution map](rust-stdlib-attribution.md) and
[Rust notices](rust-stdlib-NOTICES.txt) with this Core supplement. The Cargo map
alone does not establish complete compiler/runtime attribution coverage.

## Component and source-notice map

Each registry artifact SHA-256 below comes from the embedded inventory. Each
notice text SHA-256 identifies the exact original-text segment in
`pydantic-core-NOTICES.txt`; source links identify the retrieved upstream notice.

### ahash 0.8.12

- Registry artifact SHA-256: `5a15f179cd60c4584b8a8c596927aadc462e27f2ca70c04e0071964a73ba7a75`.
- Upstream license expression: `MIT OR Apache-2.0`.
- Distribution selection: MIT.
- [Versioned source listing](https://docs.rs/crate/ahash/0.8.12/source/).
- [LICENSE-APACHE](https://docs.rs/crate/ahash/0.8.12/source/LICENSE-APACHE) — text SHA-256 `a60eea817514531668d7e00765731449fe14d059d3249e0bc93b36de45f759f2`.
- [LICENSE-MIT](https://docs.rs/crate/ahash/0.8.12/source/LICENSE-MIT) — text SHA-256 `0444c6991eead6822f7b9102e654448d51624431119546492e8b231db42c48bb`.

### aho-corasick 1.1.3

- Registry artifact SHA-256: `8e60d3430d3a69478ad0993f19238d2df97c507009a52b3c10addcd7f6bcb916`.
- Upstream license expression: `Unlicense OR MIT`.
- Distribution selection: MIT.
- [Versioned source listing](https://docs.rs/crate/aho-corasick/1.1.3/source/).
- [COPYING](https://docs.rs/crate/aho-corasick/1.1.3/source/COPYING) — text SHA-256 `01c266bced4a434da0051174d6bee16a4c82cf634e2679b6155d40d75012390f`.
- [LICENSE-MIT](https://docs.rs/crate/aho-corasick/1.1.3/source/LICENSE-MIT) — text SHA-256 `0f96a83840e146e43c0ec96a22ec1f392e0680e6c1226e6f3ba87e0740af850f`.

### allocator-api2 0.2.21

- Registry artifact SHA-256: `683d7910e743518b0e34f1186f92494becacb047c7b6bf616c96772180fef923`.
- Upstream license expression: `MIT OR Apache-2.0`.
- Distribution selection: MIT.
- [Versioned source listing](https://docs.rs/crate/allocator-api2/0.2.21/source/).
- [LICENSE-APACHE](https://docs.rs/crate/allocator-api2/0.2.21/source/LICENSE-APACHE) — text SHA-256 `20fe7b00e904ed690e3b9fd6073784d3fc428141dbd10b81c01fd143d0797f58`.
- [LICENSE-MIT](https://docs.rs/crate/allocator-api2/0.2.21/source/LICENSE-MIT) — text SHA-256 `36516aefdc84c5d5a1e7485425913a22dbda69eb1930c5e84d6ae4972b5194b9`.

### autocfg 1.3.0

- Registry artifact SHA-256: `0c4b4d0bd25bd0b74681c0ad21497610ce1b7c91b1022cd21c80c6fbdd9476b0`.
- Upstream license expression: `Apache-2.0 OR MIT`.
- Distribution selection: MIT.
- [Versioned source listing](https://docs.rs/crate/autocfg/1.3.0/source/).
- [LICENSE-APACHE](https://docs.rs/crate/autocfg/1.3.0/source/LICENSE-APACHE) — text SHA-256 `a60eea817514531668d7e00765731449fe14d059d3249e0bc93b36de45f759f2`.
- [LICENSE-MIT](https://docs.rs/crate/autocfg/1.3.0/source/LICENSE-MIT) — text SHA-256 `27995d58ad5c1145c1a8cd86244ce844886958a35eb2b78c6b772748669999ac`.

### base64 0.22.1

- Registry artifact SHA-256: `72b3254f16251a8381aa12e40e3c4d2f0199f8c6508fbecb9d91f575e0fbb8c6`.
- Upstream license expression: `MIT OR Apache-2.0`.
- Distribution selection: MIT.
- [Versioned source listing](https://docs.rs/crate/base64/0.22.1/source/).
- [LICENSE-APACHE](https://docs.rs/crate/base64/0.22.1/source/LICENSE-APACHE) — text SHA-256 `a60eea817514531668d7e00765731449fe14d059d3249e0bc93b36de45f759f2`.
- [LICENSE-MIT](https://docs.rs/crate/base64/0.22.1/source/LICENSE-MIT) — text SHA-256 `0dd882e53de11566d50f8e8e2d5a651bcf3fabee4987d70f306233cf39094ba7`.

### bitflags 2.9.1

- Registry artifact SHA-256: `1b8e56985ec62d17e9c1001dc89c88ecd7dc08e47eba5ec7c29c7b5eeecde967`.
- Upstream license expression: `MIT OR Apache-2.0`.
- Distribution selection: MIT.
- [Versioned source listing](https://docs.rs/crate/bitflags/2.9.1/source/).
- [LICENSE-APACHE](https://docs.rs/crate/bitflags/2.9.1/source/LICENSE-APACHE) — text SHA-256 `a60eea817514531668d7e00765731449fe14d059d3249e0bc93b36de45f759f2`.
- [LICENSE-MIT](https://docs.rs/crate/bitflags/2.9.1/source/LICENSE-MIT) — text SHA-256 `6485b8ed310d3f0340bf1ad1f47645069ce4069dcc6bb46c7d5c6faf41de1fdb`.

### bitvec 1.0.1

- Registry artifact SHA-256: `1bc2832c24239b0141d5674bb9174f9d68a8b5b3f2753311927c172ca46f7e9c`.
- Upstream license expression: `MIT`.
- Distribution selection: MIT.
- [Versioned source listing](https://docs.rs/crate/bitvec/1.0.1/source/).
- [LICENSE.txt](https://docs.rs/crate/bitvec/1.0.1/source/LICENSE.txt) — text SHA-256 `411781fd38700f2357a14126d0ab048164ab881f1dcb335c1bb932e232c9a2f5`.

### bumpalo 3.19.0

- Registry artifact SHA-256: `46c5e41b57b8bba42a04676d81cb89e9ee8e859a1a66f80a5a72e1cb76b34d43`.
- Upstream license expression: `MIT OR Apache-2.0`.
- Distribution selection: MIT.
- [Versioned source listing](https://docs.rs/crate/bumpalo/3.19.0/source/).
- [LICENSE-APACHE](https://docs.rs/crate/bumpalo/3.19.0/source/LICENSE-APACHE) — text SHA-256 `a60eea817514531668d7e00765731449fe14d059d3249e0bc93b36de45f759f2`.
- [LICENSE-MIT](https://docs.rs/crate/bumpalo/3.19.0/source/LICENSE-MIT) — text SHA-256 `65f94e99ddaf4f5d1782a6dae23f35d4293a9a01444a13135a6887017d353cee`.

### cc 1.0.101

- Registry artifact SHA-256: `ac367972e516d45567c7eafc73d24e1c193dcf200a8d94e9db7b3d38b349572d`.
- Upstream license expression: `MIT OR Apache-2.0`.
- Distribution selection: MIT.
- [Versioned source listing](https://docs.rs/crate/cc/1.0.101/source/).
- [LICENSE-APACHE](https://docs.rs/crate/cc/1.0.101/source/LICENSE-APACHE) — text SHA-256 `a60eea817514531668d7e00765731449fe14d059d3249e0bc93b36de45f759f2`.
- [LICENSE-MIT](https://docs.rs/crate/cc/1.0.101/source/LICENSE-MIT) — text SHA-256 `378f5840b258e2779c39418f3f2d7b2ba96f1c7917dd6be0713f88305dbda397`.

### cfg-if 1.0.0

- Registry artifact SHA-256: `baf1de4339761588bc0619e3cbc0120ee582ebb74b53b4efbf79117bd2da40fd`.
- Upstream license expression: `MIT OR Apache-2.0`.
- Distribution selection: MIT.
- [Versioned source listing](https://docs.rs/crate/cfg-if/1.0.0/source/).
- [LICENSE-APACHE](https://docs.rs/crate/cfg-if/1.0.0/source/LICENSE-APACHE) — text SHA-256 `a60eea817514531668d7e00765731449fe14d059d3249e0bc93b36de45f759f2`.
- [LICENSE-MIT](https://docs.rs/crate/cfg-if/1.0.0/source/LICENSE-MIT) — text SHA-256 `378f5840b258e2779c39418f3f2d7b2ba96f1c7917dd6be0713f88305dbda397`.

### displaydoc 0.2.5

- Registry artifact SHA-256: `97369cbbc041bc366949bc74d34658d6cda5621039731c6310521892a3a20ae0`.
- Upstream license expression: `MIT OR Apache-2.0`.
- Distribution selection: MIT.
- [Versioned source listing](https://docs.rs/crate/displaydoc/0.2.5/source/).
- [LICENSE-APACHE](https://docs.rs/crate/displaydoc/0.2.5/source/LICENSE-APACHE) — text SHA-256 `a60eea817514531668d7e00765731449fe14d059d3249e0bc93b36de45f759f2`.
- [LICENSE-MIT](https://docs.rs/crate/displaydoc/0.2.5/source/LICENSE-MIT) — text SHA-256 `23f18e03dc49df91622fe2a76176497404e46ced8a715d9d2b67a7446571cca3`.

### enum_dispatch 0.3.13

- Registry artifact SHA-256: `aa18ce2bc66555b3218614519ac839ddb759a7d6720732f979ef8d13be147ecd`.
- Upstream license expression: `MIT OR Apache-2.0`.
- Distribution selection: MIT.
- [Versioned source listing](https://docs.rs/crate/enum_dispatch/0.3.13/source/).
- [LICENSE](https://docs.rs/crate/enum_dispatch/0.3.13/source/LICENSE) — text SHA-256 `1e5d3ddaf5cbc111c8517df39e6f252e4c16dbda26b7cc1ad54c9e3efb0586be`.

### equivalent 1.0.2

- Registry artifact SHA-256: `877a4ace8713b0bcf2a4e7eec82529c029f1d0619886d18145fea96c3ffe5c0f`.
- Upstream license expression: `Apache-2.0 OR MIT`.
- Distribution selection: MIT.
- [Versioned source listing](https://docs.rs/crate/equivalent/1.0.2/source/).
- [LICENSE-APACHE](https://docs.rs/crate/equivalent/1.0.2/source/LICENSE-APACHE) — text SHA-256 `a60eea817514531668d7e00765731449fe14d059d3249e0bc93b36de45f759f2`.
- [LICENSE-MIT](https://docs.rs/crate/equivalent/1.0.2/source/LICENSE-MIT) — text SHA-256 `7365cc8878a1d7ce155a58c4ca09c3d7a6be413efa5334a80ea842912b669349`.

### foldhash 0.2.0

- Registry artifact SHA-256: `77ce24cb58228fbb8aa041425bb1050850ac19177686ea6e0f41a70416f56fdb`.
- Upstream license expression: `Zlib`.
- Distribution selection: Zlib.
- [Versioned source listing](https://docs.rs/crate/foldhash/0.2.0/source/).
- [LICENSE](https://docs.rs/crate/foldhash/0.2.0/source/LICENSE) — text SHA-256 `b1181a40b2a7b25cf66fd01481713bc1005df082c53ef73e851e55071b102744`.

### form_urlencoded 1.2.2

- Registry artifact SHA-256: `cb4cb245038516f5f85277875cdaa4f7d2c9a0fa0468de06ed190163b1581fcf`.
- Upstream license expression: `MIT OR Apache-2.0`.
- Distribution selection: MIT.
- [Versioned source listing](https://docs.rs/crate/form_urlencoded/1.2.2/source/).
- [LICENSE-APACHE](https://docs.rs/crate/form_urlencoded/1.2.2/source/LICENSE-APACHE) — text SHA-256 `a60eea817514531668d7e00765731449fe14d059d3249e0bc93b36de45f759f2`.
- [LICENSE-MIT](https://docs.rs/crate/form_urlencoded/1.2.2/source/LICENSE-MIT) — text SHA-256 `20c7855c364d57ea4c97889a5e8d98470a9952dade37bd9248b9a54431670e5e`.

### funty 2.0.0

- Registry artifact SHA-256: `e6d5a32815ae3f33302d95fdcb2ce17862f8c65363dcfd29360480ba1001fc9c`.
- Upstream license expression: `MIT`.
- Distribution selection: MIT.
- [Versioned source listing](https://docs.rs/crate/funty/2.0.0/source/).
- [LICENSE.txt](https://docs.rs/crate/funty/2.0.0/source/LICENSE.txt) — text SHA-256 `f790cc576999f5998c766d3d26d7d64dc368e805a98461484f65e8d961ec6d9f`.

### getrandom 0.3.3

- Registry artifact SHA-256: `26145e563e54f2cadc477553f1ec5ee650b00862f0a58bcd12cbdc5f0ea2d2f4`.
- Upstream license expression: `MIT OR Apache-2.0`.
- Distribution selection: MIT.
- [Versioned source listing](https://docs.rs/crate/getrandom/0.3.3/source/).
- [LICENSE-APACHE](https://docs.rs/crate/getrandom/0.3.3/source/LICENSE-APACHE) — text SHA-256 `aaff376532ea30a0cd5330b9502ad4a4c8bf769c539c87ffe78819d188a18ebf`.
- [LICENSE-MIT](https://docs.rs/crate/getrandom/0.3.3/source/LICENSE-MIT) — text SHA-256 `29e9fe5074bd27e0e5d5d110394fbbcd841baee2651a3c4b4560a632702cede4`.

### hashbrown 0.16.1

- Registry artifact SHA-256: `841d1cc9bed7f9236f321df977030373f4a4163ae1a7dbfe1a51a2c1a51d9100`.
- Upstream license expression: `MIT OR Apache-2.0`.
- Distribution selection: MIT.
- [Versioned source listing](https://docs.rs/crate/hashbrown/0.16.1/source/).
- [LICENSE-APACHE](https://docs.rs/crate/hashbrown/0.16.1/source/LICENSE-APACHE) — text SHA-256 `a60eea817514531668d7e00765731449fe14d059d3249e0bc93b36de45f759f2`.
- [LICENSE-MIT](https://docs.rs/crate/hashbrown/0.16.1/source/LICENSE-MIT) — text SHA-256 `ff8f68cb076caf8cefe7a6430d4ac086ce6af2ca8ce2c4e5a2004d4552ef52a2`.

### heck 0.5.0

- Registry artifact SHA-256: `2304e00983f87ffb38b55b444b5e3b60a884b5d30c0fca7d82fe33449bbe55ea`.
- Upstream license expression: `MIT OR Apache-2.0`.
- Distribution selection: MIT.
- [Versioned source listing](https://docs.rs/crate/heck/0.5.0/source/).
- [LICENSE-APACHE](https://docs.rs/crate/heck/0.5.0/source/LICENSE-APACHE) — text SHA-256 `a60eea817514531668d7e00765731449fe14d059d3249e0bc93b36de45f759f2`.
- [LICENSE-MIT](https://docs.rs/crate/heck/0.5.0/source/LICENSE-MIT) — text SHA-256 `7b63ecd5f1902af1b63729947373683c32745c16a10e8e6292e2e2dcd7e90ae0`.

### hex 0.4.3

- Registry artifact SHA-256: `7f24254aa9a54b5c858eaee2f5bccdb46aaf0e486a595ed5fd8f86ba55232a70`.
- Upstream license expression: `MIT OR Apache-2.0`.
- Distribution selection: MIT.
- [Versioned source listing](https://docs.rs/crate/hex/0.4.3/source/).
- [LICENSE-APACHE](https://docs.rs/crate/hex/0.4.3/source/LICENSE-APACHE) — text SHA-256 `c6596eb7be8581c18be736c846fb9173b69eccf6ef94c5135893ec56bd92ba08`.
- [LICENSE-MIT](https://docs.rs/crate/hex/0.4.3/source/LICENSE-MIT) — text SHA-256 `f7bdb3426d045cd50efd4953026e3eb5a83d0199f458a075602611b9344da5b9`.

### icu_collections 1.5.0

- Registry artifact SHA-256: `db2fa452206ebee18c4b5c2274dbf1de17008e874b4dc4f0aea9d01ca79e4526`.
- Upstream license expression: `Unicode-3.0`.
- Distribution selection: Unicode-3.0.
- [Versioned source listing](https://docs.rs/crate/icu_collections/1.5.0/source/).
- [LICENSE](https://docs.rs/crate/icu_collections/1.5.0/source/LICENSE) — text SHA-256 `f367c1b8e1aa262435251e442901da4607b4650e0e63a026f5044473ecfb90f2`.

### icu_locid 1.5.0

- Registry artifact SHA-256: `13acbb8371917fc971be86fc8057c41a64b521c184808a698c02acc242dbf637`.
- Upstream license expression: `Unicode-3.0`.
- Distribution selection: Unicode-3.0.
- [Versioned source listing](https://docs.rs/crate/icu_locid/1.5.0/source/).
- [LICENSE](https://docs.rs/crate/icu_locid/1.5.0/source/LICENSE) — text SHA-256 `f367c1b8e1aa262435251e442901da4607b4650e0e63a026f5044473ecfb90f2`.

### icu_locid_transform 1.5.0

- Registry artifact SHA-256: `01d11ac35de8e40fdeda00d9e1e9d92525f3f9d887cdd7aa81d727596788b54e`.
- Upstream license expression: `Unicode-3.0`.
- Distribution selection: Unicode-3.0.
- [Versioned source listing](https://docs.rs/crate/icu_locid_transform/1.5.0/source/).
- [LICENSE](https://docs.rs/crate/icu_locid_transform/1.5.0/source/LICENSE) — text SHA-256 `f367c1b8e1aa262435251e442901da4607b4650e0e63a026f5044473ecfb90f2`.

### icu_locid_transform_data 1.5.0

- Registry artifact SHA-256: `fdc8ff3388f852bede6b579ad4e978ab004f139284d7b28715f773507b946f6e`.
- Upstream license expression: `Unicode-3.0`.
- Distribution selection: Unicode-3.0.
- [Versioned source listing](https://docs.rs/crate/icu_locid_transform_data/1.5.0/source/).
- [LICENSE](https://docs.rs/crate/icu_locid_transform_data/1.5.0/source/LICENSE) — text SHA-256 `f367c1b8e1aa262435251e442901da4607b4650e0e63a026f5044473ecfb90f2`.

### icu_normalizer 1.5.0

- Registry artifact SHA-256: `19ce3e0da2ec68599d193c93d088142efd7f9c5d6fc9b803774855747dc6a84f`.
- Upstream license expression: `Unicode-3.0`.
- Distribution selection: Unicode-3.0.
- [Versioned source listing](https://docs.rs/crate/icu_normalizer/1.5.0/source/).
- [LICENSE](https://docs.rs/crate/icu_normalizer/1.5.0/source/LICENSE) — text SHA-256 `f367c1b8e1aa262435251e442901da4607b4650e0e63a026f5044473ecfb90f2`.

### icu_normalizer_data 1.5.0

- Registry artifact SHA-256: `f8cafbf7aa791e9b22bec55a167906f9e1215fd475cd22adfcf660e03e989516`.
- Upstream license expression: `Unicode-3.0`.
- Distribution selection: Unicode-3.0.
- [Versioned source listing](https://docs.rs/crate/icu_normalizer_data/1.5.0/source/).
- [LICENSE](https://docs.rs/crate/icu_normalizer_data/1.5.0/source/LICENSE) — text SHA-256 `f367c1b8e1aa262435251e442901da4607b4650e0e63a026f5044473ecfb90f2`.

### icu_properties 1.5.1

- Registry artifact SHA-256: `93d6020766cfc6302c15dbbc9c8778c37e62c14427cb7f6e601d849e092aeef5`.
- Upstream license expression: `Unicode-3.0`.
- Distribution selection: Unicode-3.0.
- [Versioned source listing](https://docs.rs/crate/icu_properties/1.5.1/source/).
- [LICENSE](https://docs.rs/crate/icu_properties/1.5.1/source/LICENSE) — text SHA-256 `f367c1b8e1aa262435251e442901da4607b4650e0e63a026f5044473ecfb90f2`.

### icu_properties_data 1.5.0

- Registry artifact SHA-256: `67a8effbc3dd3e4ba1afa8ad918d5684b8868b3b26500753effea8d2eed19569`.
- Upstream license expression: `Unicode-3.0`.
- Distribution selection: Unicode-3.0.
- [Versioned source listing](https://docs.rs/crate/icu_properties_data/1.5.0/source/).
- [LICENSE](https://docs.rs/crate/icu_properties_data/1.5.0/source/LICENSE) — text SHA-256 `f367c1b8e1aa262435251e442901da4607b4650e0e63a026f5044473ecfb90f2`.

### icu_provider 1.5.0

- Registry artifact SHA-256: `6ed421c8a8ef78d3e2dbc98a973be2f3770cb42b606e3ab18d6237c4dfde68d9`.
- Upstream license expression: `Unicode-3.0`.
- Distribution selection: Unicode-3.0.
- [Versioned source listing](https://docs.rs/crate/icu_provider/1.5.0/source/).
- [LICENSE](https://docs.rs/crate/icu_provider/1.5.0/source/LICENSE) — text SHA-256 `f367c1b8e1aa262435251e442901da4607b4650e0e63a026f5044473ecfb90f2`.

### icu_provider_macros 1.5.0

- Registry artifact SHA-256: `1ec89e9337638ecdc08744df490b221a7399bf8d164eb52a665454e60e075ad6`.
- Upstream license expression: `Unicode-3.0`.
- Distribution selection: Unicode-3.0.
- [Versioned source listing](https://docs.rs/crate/icu_provider_macros/1.5.0/source/).
- [LICENSE](https://docs.rs/crate/icu_provider_macros/1.5.0/source/LICENSE) — text SHA-256 `f367c1b8e1aa262435251e442901da4607b4650e0e63a026f5044473ecfb90f2`.

### idna 1.1.0

- Registry artifact SHA-256: `3b0875f23caa03898994f6ddc501886a45c7d3d62d04d2d90788d47be1b1e4de`.
- Upstream license expression: `MIT OR Apache-2.0`.
- Distribution selection: MIT.
- [Versioned source listing](https://docs.rs/crate/idna/1.1.0/source/).
- [LICENSE-APACHE](https://docs.rs/crate/idna/1.1.0/source/LICENSE-APACHE) — text SHA-256 `a60eea817514531668d7e00765731449fe14d059d3249e0bc93b36de45f759f2`.
- [LICENSE-MIT](https://docs.rs/crate/idna/1.1.0/source/LICENSE-MIT) — text SHA-256 `b38f11f6096706e6de553dabe2a7ed142d59b6fa8c97e290c67496154745cdd5`.

### idna_adapter 1.2.0

- Registry artifact SHA-256: `daca1df1c957320b2cf139ac61e7bd64fed304c5040df000a745aa1de3b4ef71`.
- Upstream license expression: `Apache-2.0 OR MIT`.
- Distribution selection: MIT.
- [Versioned source listing](https://docs.rs/crate/idna_adapter/1.2.0/source/).
- [LICENSE-APACHE](https://docs.rs/crate/idna_adapter/1.2.0/source/LICENSE-APACHE) — text SHA-256 `a60eea817514531668d7e00765731449fe14d059d3249e0bc93b36de45f759f2`.
- [LICENSE-MIT](https://docs.rs/crate/idna_adapter/1.2.0/source/LICENSE-MIT) — text SHA-256 `8b43ce8accd61e9d370b5ca9e9c4f953279b5c239926c62315b40e24df51b726`.

### itoa 1.0.11

- Registry artifact SHA-256: `49f1f14873335454500d59611f1cf4a4b0f786f9ac11f4312a78e4cf2566695b`.
- Upstream license expression: `MIT OR Apache-2.0`.
- Distribution selection: MIT.
- [Versioned source listing](https://docs.rs/crate/itoa/1.0.11/source/).
- [LICENSE-APACHE](https://docs.rs/crate/itoa/1.0.11/source/LICENSE-APACHE) — text SHA-256 `62c7a1e35f56406896d7aa7ca52d0cc0d272ac022b5d2796e7d6905db8a3636a`.
- [LICENSE-MIT](https://docs.rs/crate/itoa/1.0.11/source/LICENSE-MIT) — text SHA-256 `23f18e03dc49df91622fe2a76176497404e46ced8a715d9d2b67a7446571cca3`.

### jiter 0.14.0

- Registry artifact SHA-256: `b6f3b5d3f84b36f4ad09fd1da896d23d9852a1aa86556578dd0289f43dce311d`.
- Upstream license expression: `MIT`.
- Distribution selection: MIT.
- [Versioned source listing](https://docs.rs/crate/jiter/0.14.0/source/).
- [LICENSE](https://docs.rs/crate/jiter/0.14.0/source/LICENSE) — text SHA-256 `7c7134b9f7b978c03fca875517cf398db91f19bbb8109b6685e742aa3f57468e`.

### js-sys 0.3.77

- Registry artifact SHA-256: `1cfaf33c695fc6e08064efbc1f72ec937429614f25eef83af942d0e227c3a28f`.
- Upstream license expression: `MIT OR Apache-2.0`.
- Distribution selection: MIT.
- [Versioned source listing](https://docs.rs/crate/js-sys/0.3.77/source/).
- [LICENSE-APACHE](https://docs.rs/crate/js-sys/0.3.77/source/LICENSE-APACHE) — text SHA-256 `a60eea817514531668d7e00765731449fe14d059d3249e0bc93b36de45f759f2`.
- [LICENSE-MIT](https://docs.rs/crate/js-sys/0.3.77/source/LICENSE-MIT) — text SHA-256 `378f5840b258e2779c39418f3f2d7b2ba96f1c7917dd6be0713f88305dbda397`.

### lexical-parse-float 1.0.5

- Registry artifact SHA-256: `de6f9cb01fb0b08060209a057c048fcbab8717b4c1ecd2eac66ebfe39a65b0f2`.
- Upstream license expression: `MIT OR Apache-2.0`.
- Distribution selection: MIT; preserve feature-dependent portions described above.
- [Versioned source listing](https://docs.rs/crate/lexical-parse-float/1.0.5/source/).
- [LICENSE-APACHE](https://docs.rs/crate/lexical-parse-float/1.0.5/source/LICENSE-APACHE) — text SHA-256 `8173d5c29b4f956d532781d2b86e4e30f83e6b7878dce18c919451d6ba707c90`.
- [LICENSE-MIT](https://docs.rs/crate/lexical-parse-float/1.0.5/source/LICENSE-MIT) — text SHA-256 `23f18e03dc49df91622fe2a76176497404e46ced8a715d9d2b67a7446571cca3`.
- [LICENSE.md](https://docs.rs/crate/lexical-parse-float/1.0.5/source/LICENSE.md) — text SHA-256 `99aab70a96f3ecab683d8a319d9070af2213ae4c1601fb053fbe85e1361e32fe`.

### lexical-parse-integer 1.0.5

- Registry artifact SHA-256: `72207aae22fc0a121ba7b6d479e42cbfea549af1479c3f3a4f12c70dd66df12e`.
- Upstream license expression: `MIT OR Apache-2.0`.
- Distribution selection: MIT; preserve feature-dependent portions described above.
- [Versioned source listing](https://docs.rs/crate/lexical-parse-integer/1.0.5/source/).
- [LICENSE-APACHE](https://docs.rs/crate/lexical-parse-integer/1.0.5/source/LICENSE-APACHE) — text SHA-256 `8173d5c29b4f956d532781d2b86e4e30f83e6b7878dce18c919451d6ba707c90`.
- [LICENSE-MIT](https://docs.rs/crate/lexical-parse-integer/1.0.5/source/LICENSE-MIT) — text SHA-256 `23f18e03dc49df91622fe2a76176497404e46ced8a715d9d2b67a7446571cca3`.
- [LICENSE.md](https://docs.rs/crate/lexical-parse-integer/1.0.5/source/LICENSE.md) — text SHA-256 `99aab70a96f3ecab683d8a319d9070af2213ae4c1601fb053fbe85e1361e32fe`.

### lexical-util 1.0.6

- Registry artifact SHA-256: `5a82e24bf537fd24c177ffbbdc6ebcc8d54732c35b50a3f28cc3f4e4c949a0b3`.
- Upstream license expression: `MIT OR Apache-2.0`.
- Distribution selection: MIT; preserve feature-dependent portions described above.
- [Versioned source listing](https://docs.rs/crate/lexical-util/1.0.6/source/).
- [LICENSE-APACHE](https://docs.rs/crate/lexical-util/1.0.6/source/LICENSE-APACHE) — text SHA-256 `8173d5c29b4f956d532781d2b86e4e30f83e6b7878dce18c919451d6ba707c90`.
- [LICENSE-MIT](https://docs.rs/crate/lexical-util/1.0.6/source/LICENSE-MIT) — text SHA-256 `23f18e03dc49df91622fe2a76176497404e46ced8a715d9d2b67a7446571cca3`.

### libc 0.2.185

- Registry artifact SHA-256: `52ff2c0fe9bc6cb6b14a0592c2ff4fa9ceb83eea9db979b0487cd054946a2b8f`.
- Upstream license expression: `MIT OR Apache-2.0`.
- Distribution selection: MIT.
- [Versioned source listing](https://docs.rs/crate/libc/0.2.185/source/).
- [LICENSE-APACHE](https://docs.rs/crate/libc/0.2.185/source/LICENSE-APACHE) — text SHA-256 `62c7a1e35f56406896d7aa7ca52d0cc0d272ac022b5d2796e7d6905db8a3636a`.
- [LICENSE-MIT](https://docs.rs/crate/libc/0.2.185/source/LICENSE-MIT) — text SHA-256 `123a331b5dbf04c30097fa43b8f858bc85df671fe776de498d01f3d6b7c1f69e`.

### litemap 0.7.3

- Registry artifact SHA-256: `643cb0b8d4fcc284004d5fd0d67ccf61dfffadb7f75e1e71bc420f4688a3a704`.
- Upstream license expression: `Unicode-3.0`.
- Distribution selection: Unicode-3.0.
- [Versioned source listing](https://docs.rs/crate/litemap/0.7.3/source/).
- [LICENSE](https://docs.rs/crate/litemap/0.7.3/source/LICENSE) — text SHA-256 `f367c1b8e1aa262435251e442901da4607b4650e0e63a026f5044473ecfb90f2`.

### log 0.4.27

- Registry artifact SHA-256: `13dc2df351e3202783a1fe0d44375f7295ffb4049267b0f3018346dc122a1d94`.
- Upstream license expression: `MIT OR Apache-2.0`.
- Distribution selection: MIT.
- [Versioned source listing](https://docs.rs/crate/log/0.4.27/source/).
- [LICENSE-APACHE](https://docs.rs/crate/log/0.4.27/source/LICENSE-APACHE) — text SHA-256 `a60eea817514531668d7e00765731449fe14d059d3249e0bc93b36de45f759f2`.
- [LICENSE-MIT](https://docs.rs/crate/log/0.4.27/source/LICENSE-MIT) — text SHA-256 `6485b8ed310d3f0340bf1ad1f47645069ce4069dcc6bb46c7d5c6faf41de1fdb`.

### lru 0.16.3

- Registry artifact SHA-256: `a1dc47f592c06f33f8e3aea9591776ec7c9f9e4124778ff8a3c3b87159f7e593`.
- Upstream license expression: `MIT`.
- Distribution selection: MIT.
- [Versioned source listing](https://docs.rs/crate/lru/0.16.3/source/).
- [LICENSE](https://docs.rs/crate/lru/0.16.3/source/LICENSE) — text SHA-256 `061dc50af2cd9340703daf61978af3200cf681b12ea67a323c33ba109a23a45e`.

### memchr 2.7.4

- Registry artifact SHA-256: `78ca9ab1a0babb1e7d5695e3530886289c18cf2f87ec19a575a0abdce112e3a3`.
- Upstream license expression: `Unlicense OR MIT`.
- Distribution selection: MIT.
- [Versioned source listing](https://docs.rs/crate/memchr/2.7.4/source/).
- [COPYING](https://docs.rs/crate/memchr/2.7.4/source/COPYING) — text SHA-256 `01c266bced4a434da0051174d6bee16a4c82cf634e2679b6155d40d75012390f`.
- [LICENSE-MIT](https://docs.rs/crate/memchr/2.7.4/source/LICENSE-MIT) — text SHA-256 `0f96a83840e146e43c0ec96a22ec1f392e0680e6c1226e6f3ba87e0740af850f`.

### num-bigint 0.4.6

- Registry artifact SHA-256: `a5e44f723f1133c9deac646763579fdb3ac745e418f2a7af9cd0c431da1f20b9`.
- Upstream license expression: `MIT OR Apache-2.0`.
- Distribution selection: MIT.
- [Versioned source listing](https://docs.rs/crate/num-bigint/0.4.6/source/).
- [LICENSE-APACHE](https://docs.rs/crate/num-bigint/0.4.6/source/LICENSE-APACHE) — text SHA-256 `a60eea817514531668d7e00765731449fe14d059d3249e0bc93b36de45f759f2`.
- [LICENSE-MIT](https://docs.rs/crate/num-bigint/0.4.6/source/LICENSE-MIT) — text SHA-256 `6485b8ed310d3f0340bf1ad1f47645069ce4069dcc6bb46c7d5c6faf41de1fdb`.

### num-integer 0.1.46

- Registry artifact SHA-256: `7969661fd2958a5cb096e56c8e1ad0444ac2bbcd0061bd28660485a44879858f`.
- Upstream license expression: `MIT OR Apache-2.0`.
- Distribution selection: MIT.
- [Versioned source listing](https://docs.rs/crate/num-integer/0.1.46/source/).
- [LICENSE-APACHE](https://docs.rs/crate/num-integer/0.1.46/source/LICENSE-APACHE) — text SHA-256 `a60eea817514531668d7e00765731449fe14d059d3249e0bc93b36de45f759f2`.
- [LICENSE-MIT](https://docs.rs/crate/num-integer/0.1.46/source/LICENSE-MIT) — text SHA-256 `6485b8ed310d3f0340bf1ad1f47645069ce4069dcc6bb46c7d5c6faf41de1fdb`.

### num-traits 0.2.19

- Registry artifact SHA-256: `071dfc062690e90b734c0b2273ce72ad0ffa95f0c74596bc250dcfd960262841`.
- Upstream license expression: `MIT OR Apache-2.0`.
- Distribution selection: MIT.
- [Versioned source listing](https://docs.rs/crate/num-traits/0.2.19/source/).
- [LICENSE-APACHE](https://docs.rs/crate/num-traits/0.2.19/source/LICENSE-APACHE) — text SHA-256 `a60eea817514531668d7e00765731449fe14d059d3249e0bc93b36de45f759f2`.
- [LICENSE-MIT](https://docs.rs/crate/num-traits/0.2.19/source/LICENSE-MIT) — text SHA-256 `6485b8ed310d3f0340bf1ad1f47645069ce4069dcc6bb46c7d5c6faf41de1fdb`.

### once_cell 1.21.3

- Registry artifact SHA-256: `42f5e15c9953c5e4ccceeb2e7382a716482c34515315f7b03532b8b4e8393d2d`.
- Upstream license expression: `MIT OR Apache-2.0`.
- Distribution selection: MIT.
- [Versioned source listing](https://docs.rs/crate/once_cell/1.21.3/source/).
- [LICENSE-APACHE](https://docs.rs/crate/once_cell/1.21.3/source/LICENSE-APACHE) — text SHA-256 `a60eea817514531668d7e00765731449fe14d059d3249e0bc93b36de45f759f2`.
- [LICENSE-MIT](https://docs.rs/crate/once_cell/1.21.3/source/LICENSE-MIT) — text SHA-256 `23f18e03dc49df91622fe2a76176497404e46ced8a715d9d2b67a7446571cca3`.

### percent-encoding 2.3.2

- Registry artifact SHA-256: `9b4f627cb1b25917193a259e49bdad08f671f8d9708acfd5fe0a8c1455d87220`.
- Upstream license expression: `MIT OR Apache-2.0`.
- Distribution selection: MIT.
- [Versioned source listing](https://docs.rs/crate/percent-encoding/2.3.2/source/).
- [LICENSE-APACHE](https://docs.rs/crate/percent-encoding/2.3.2/source/LICENSE-APACHE) — text SHA-256 `a60eea817514531668d7e00765731449fe14d059d3249e0bc93b36de45f759f2`.
- [LICENSE-MIT](https://docs.rs/crate/percent-encoding/2.3.2/source/LICENSE-MIT) — text SHA-256 `b38f11f6096706e6de553dabe2a7ed142d59b6fa8c97e290c67496154745cdd5`.

### portable-atomic 1.6.0

- Registry artifact SHA-256: `7170ef9988bc169ba16dd36a7fa041e5c4cbeb6a35b76d4c03daded371eae7c0`.
- Upstream license expression: `Apache-2.0 OR MIT`.
- Distribution selection: MIT.
- [Versioned source listing](https://docs.rs/crate/portable-atomic/1.6.0/source/).
- [LICENSE-APACHE](https://docs.rs/crate/portable-atomic/1.6.0/source/LICENSE-APACHE) — text SHA-256 `0d542e0c8804e39aa7f37eb00da5a762149dc682d7829451287e11b938e94594`.
- [LICENSE-MIT](https://docs.rs/crate/portable-atomic/1.6.0/source/LICENSE-MIT) — text SHA-256 `23f18e03dc49df91622fe2a76176497404e46ced8a715d9d2b67a7446571cca3`.

### proc-macro2 1.0.86

- Registry artifact SHA-256: `5e719e8df665df0d1c8fbfd238015744736151d4445ec0836b8e628aae103b77`.
- Upstream license expression: `MIT OR Apache-2.0`.
- Distribution selection: MIT.
- [Versioned source listing](https://docs.rs/crate/proc-macro2/1.0.86/source/).
- [LICENSE-APACHE](https://docs.rs/crate/proc-macro2/1.0.86/source/LICENSE-APACHE) — text SHA-256 `62c7a1e35f56406896d7aa7ca52d0cc0d272ac022b5d2796e7d6905db8a3636a`.
- [LICENSE-MIT](https://docs.rs/crate/proc-macro2/1.0.86/source/LICENSE-MIT) — text SHA-256 `23f18e03dc49df91622fe2a76176497404e46ced8a715d9d2b67a7446571cca3`.

### pyo3 0.28.3

- Registry artifact SHA-256: `91fd8e38a3b50ed1167fb981cd6fd60147e091784c427b8f7183a7ee32c31c12`.
- Upstream license expression: `MIT OR Apache-2.0`.
- Distribution selection: MIT.
- [Versioned source listing](https://docs.rs/crate/pyo3/0.28.3/source/).
- [LICENSE-APACHE](https://docs.rs/crate/pyo3/0.28.3/source/LICENSE-APACHE) — text SHA-256 `32c76dbe0e73d79100d5ece77c158399f2e2541bc5c78548a4ba45c1cb53c5c9`.
- [LICENSE-MIT](https://docs.rs/crate/pyo3/0.28.3/source/LICENSE-MIT) — text SHA-256 `afcbe3b2e6b37172b5a9ca869ee4c0b8cdc09316e5d4384864154482c33e5af6`.

### pyo3-build-config 0.28.3

- Registry artifact SHA-256: `e368e7ddfdeb98c9bca7f8383be1648fd84ab466bf2bc015e94008db6d35611e`.
- Upstream license expression: `MIT OR Apache-2.0`.
- Distribution selection: MIT.
- [Versioned source listing](https://docs.rs/crate/pyo3-build-config/0.28.3/source/).
- [LICENSE-APACHE](https://docs.rs/crate/pyo3-build-config/0.28.3/source/LICENSE-APACHE) — text SHA-256 `32c76dbe0e73d79100d5ece77c158399f2e2541bc5c78548a4ba45c1cb53c5c9`.
- [LICENSE-MIT](https://docs.rs/crate/pyo3-build-config/0.28.3/source/LICENSE-MIT) — text SHA-256 `afcbe3b2e6b37172b5a9ca869ee4c0b8cdc09316e5d4384864154482c33e5af6`.

### pyo3-ffi 0.28.3

- Registry artifact SHA-256: `7f29e10af80b1f7ccaf7f69eace800a03ecd13e883acfacc1e5d0988605f651e`.
- Upstream license expression: `MIT OR Apache-2.0`.
- Distribution selection: MIT.
- [Versioned source listing](https://docs.rs/crate/pyo3-ffi/0.28.3/source/).
- [LICENSE-APACHE](https://docs.rs/crate/pyo3-ffi/0.28.3/source/LICENSE-APACHE) — text SHA-256 `32c76dbe0e73d79100d5ece77c158399f2e2541bc5c78548a4ba45c1cb53c5c9`.
- [LICENSE-MIT](https://docs.rs/crate/pyo3-ffi/0.28.3/source/LICENSE-MIT) — text SHA-256 `afcbe3b2e6b37172b5a9ca869ee4c0b8cdc09316e5d4384864154482c33e5af6`.

### pyo3-macros 0.28.3

- Registry artifact SHA-256: `df6e520eff47c45997d2fc7dd8214b25dd1310918bbb2642156ef66a67f29813`.
- Upstream license expression: `MIT OR Apache-2.0`.
- Distribution selection: MIT.
- [Versioned source listing](https://docs.rs/crate/pyo3-macros/0.28.3/source/).
- [LICENSE-APACHE](https://docs.rs/crate/pyo3-macros/0.28.3/source/LICENSE-APACHE) — text SHA-256 `32c76dbe0e73d79100d5ece77c158399f2e2541bc5c78548a4ba45c1cb53c5c9`.
- [LICENSE-MIT](https://docs.rs/crate/pyo3-macros/0.28.3/source/LICENSE-MIT) — text SHA-256 `afcbe3b2e6b37172b5a9ca869ee4c0b8cdc09316e5d4384864154482c33e5af6`.

### pyo3-macros-backend 0.28.3

- Registry artifact SHA-256: `c4cdc218d835738f81c2338f822078af45b4afdf8b2e33cbb5916f108b813acb`.
- Upstream license expression: `MIT OR Apache-2.0`.
- Distribution selection: MIT.
- [Versioned source listing](https://docs.rs/crate/pyo3-macros-backend/0.28.3/source/).
- [LICENSE-APACHE](https://docs.rs/crate/pyo3-macros-backend/0.28.3/source/LICENSE-APACHE) — text SHA-256 `32c76dbe0e73d79100d5ece77c158399f2e2541bc5c78548a4ba45c1cb53c5c9`.
- [LICENSE-MIT](https://docs.rs/crate/pyo3-macros-backend/0.28.3/source/LICENSE-MIT) — text SHA-256 `afcbe3b2e6b37172b5a9ca869ee4c0b8cdc09316e5d4384864154482c33e5af6`.

### python3-dll-a 0.2.14

- Registry artifact SHA-256: `d381ef313ae70b4da5f95f8a4de773c6aa5cd28f73adec4b4a31df70b66780d8`.
- Upstream license expression: `MIT`.
- Distribution selection: MIT.
- [Versioned source listing](https://docs.rs/crate/python3-dll-a/0.2.14/source/).
- [LICENSE](https://docs.rs/crate/python3-dll-a/0.2.14/source/LICENSE) — text SHA-256 `c760d1f2e4614d58f2b91cd41b9826457989c09041be0219158b8f7f6d3f8763`.

### quote 1.0.44

- Registry artifact SHA-256: `21b2ebcf727b7760c461f091f9f0f539b77b8e87f2fd88131e7f1b433b3cece4`.
- Upstream license expression: `MIT OR Apache-2.0`.
- Distribution selection: MIT.
- [Versioned source listing](https://docs.rs/crate/quote/1.0.44/source/).
- [LICENSE-APACHE](https://docs.rs/crate/quote/1.0.44/source/LICENSE-APACHE) — text SHA-256 `62c7a1e35f56406896d7aa7ca52d0cc0d272ac022b5d2796e7d6905db8a3636a`.
- [LICENSE-MIT](https://docs.rs/crate/quote/1.0.44/source/LICENSE-MIT) — text SHA-256 `23f18e03dc49df91622fe2a76176497404e46ced8a715d9d2b67a7446571cca3`.

### r-efi 5.2.0

- Registry artifact SHA-256: `74765f6d916ee2faa39bc8e68e4f3ed8949b48cccdac59983d287a7cb71ce9c5`.
- Upstream license expression: `MIT OR Apache-2.0 OR LGPL-2.1-or-later`.
- Distribution selection: MIT.
- [Versioned source listing](https://docs.rs/crate/r-efi/5.2.0/source/).
- [AUTHORS](https://docs.rs/crate/r-efi/5.2.0/source/AUTHORS) — text SHA-256 `c42285aa32440c3b5358fd4805b817cd8d6e1795111d2e4c547530be41896b2d`.

### radium 0.7.0

- Registry artifact SHA-256: `dc33ff2d4973d518d823d61aa239014831e521c75da58e3df4840d3f47749d09`.
- Upstream license expression: `MIT`.
- Distribution selection: MIT.
- [Versioned source listing](https://docs.rs/crate/radium/0.7.0/source/).
- [LICENSE.txt](https://docs.rs/crate/radium/0.7.0/source/LICENSE.txt) — text SHA-256 `13f4cc9fbc8d4a447b28aa84019c10ad4abf4b5f6919db061bf6690ccc23bc02`.

### regex 1.12.3

- Registry artifact SHA-256: `e10754a14b9137dd7b1e3e5b0493cc9171fdd105e0ab477f51b72e7f3ac0e276`.
- Upstream license expression: `MIT OR Apache-2.0`.
- Distribution selection: MIT.
- [Versioned source listing](https://docs.rs/crate/regex/1.12.3/source/).
- [LICENSE-APACHE](https://docs.rs/crate/regex/1.12.3/source/LICENSE-APACHE) — text SHA-256 `a60eea817514531668d7e00765731449fe14d059d3249e0bc93b36de45f759f2`.
- [LICENSE-MIT](https://docs.rs/crate/regex/1.12.3/source/LICENSE-MIT) — text SHA-256 `6485b8ed310d3f0340bf1ad1f47645069ce4069dcc6bb46c7d5c6faf41de1fdb`.

### regex-automata 0.4.13

- Registry artifact SHA-256: `5276caf25ac86c8d810222b3dbb938e512c55c6831a10f3e6ed1c93b84041f1c`.
- Upstream license expression: `MIT OR Apache-2.0`.
- Distribution selection: MIT.
- [Versioned source listing](https://docs.rs/crate/regex-automata/0.4.13/source/).
- [LICENSE-APACHE](https://docs.rs/crate/regex-automata/0.4.13/source/LICENSE-APACHE) — text SHA-256 `a60eea817514531668d7e00765731449fe14d059d3249e0bc93b36de45f759f2`.
- [LICENSE-MIT](https://docs.rs/crate/regex-automata/0.4.13/source/LICENSE-MIT) — text SHA-256 `6485b8ed310d3f0340bf1ad1f47645069ce4069dcc6bb46c7d5c6faf41de1fdb`.

### regex-syntax 0.8.5

- Registry artifact SHA-256: `2b15c43186be67a4fd63bee50d0303afffcef381492ebe2c5d87f324e1b8815c`.
- Upstream license expression: `MIT OR Apache-2.0`.
- Distribution selection: MIT.
- [Versioned source listing](https://docs.rs/crate/regex-syntax/0.8.5/source/).
- [LICENSE-APACHE](https://docs.rs/crate/regex-syntax/0.8.5/source/LICENSE-APACHE) — text SHA-256 `a60eea817514531668d7e00765731449fe14d059d3249e0bc93b36de45f759f2`.
- [LICENSE-MIT](https://docs.rs/crate/regex-syntax/0.8.5/source/LICENSE-MIT) — text SHA-256 `6485b8ed310d3f0340bf1ad1f47645069ce4069dcc6bb46c7d5c6faf41de1fdb`.

### rustversion 1.0.17

- Registry artifact SHA-256: `955d28af4278de8121b7ebeb796b6a45735dc01436d898801014aced2773a3d6`.
- Upstream license expression: `MIT OR Apache-2.0`.
- Distribution selection: MIT.
- [Versioned source listing](https://docs.rs/crate/rustversion/1.0.17/source/).
- [LICENSE-APACHE](https://docs.rs/crate/rustversion/1.0.17/source/LICENSE-APACHE) — text SHA-256 `62c7a1e35f56406896d7aa7ca52d0cc0d272ac022b5d2796e7d6905db8a3636a`.
- [LICENSE-MIT](https://docs.rs/crate/rustversion/1.0.17/source/LICENSE-MIT) — text SHA-256 `23f18e03dc49df91622fe2a76176497404e46ced8a715d9d2b67a7446571cca3`.

### serde 1.0.228

- Registry artifact SHA-256: `9a8e94ea7f378bd32cbbd37198a4a91436180c5bb472411e48b5ec2e2124ae9e`.
- Upstream license expression: `MIT OR Apache-2.0`.
- Distribution selection: MIT.
- [Versioned source listing](https://docs.rs/crate/serde/1.0.228/source/).
- [LICENSE-APACHE](https://docs.rs/crate/serde/1.0.228/source/LICENSE-APACHE) — text SHA-256 `62c7a1e35f56406896d7aa7ca52d0cc0d272ac022b5d2796e7d6905db8a3636a`.
- [LICENSE-MIT](https://docs.rs/crate/serde/1.0.228/source/LICENSE-MIT) — text SHA-256 `23f18e03dc49df91622fe2a76176497404e46ced8a715d9d2b67a7446571cca3`.

### serde_core 1.0.228

- Registry artifact SHA-256: `41d385c7d4ca58e59fc732af25c3983b67ac852c1a25000afe1175de458b67ad`.
- Upstream license expression: `MIT OR Apache-2.0`.
- Distribution selection: MIT.
- [Versioned source listing](https://docs.rs/crate/serde_core/1.0.228/source/).
- [LICENSE-APACHE](https://docs.rs/crate/serde_core/1.0.228/source/LICENSE-APACHE) — text SHA-256 `62c7a1e35f56406896d7aa7ca52d0cc0d272ac022b5d2796e7d6905db8a3636a`.
- [LICENSE-MIT](https://docs.rs/crate/serde_core/1.0.228/source/LICENSE-MIT) — text SHA-256 `23f18e03dc49df91622fe2a76176497404e46ced8a715d9d2b67a7446571cca3`.

### serde_derive 1.0.228

- Registry artifact SHA-256: `d540f220d3187173da220f885ab66608367b6574e925011a9353e4badda91d79`.
- Upstream license expression: `MIT OR Apache-2.0`.
- Distribution selection: MIT.
- [Versioned source listing](https://docs.rs/crate/serde_derive/1.0.228/source/).
- [LICENSE-APACHE](https://docs.rs/crate/serde_derive/1.0.228/source/LICENSE-APACHE) — text SHA-256 `62c7a1e35f56406896d7aa7ca52d0cc0d272ac022b5d2796e7d6905db8a3636a`.
- [LICENSE-MIT](https://docs.rs/crate/serde_derive/1.0.228/source/LICENSE-MIT) — text SHA-256 `23f18e03dc49df91622fe2a76176497404e46ced8a715d9d2b67a7446571cca3`.

### serde_json 1.0.149

- Registry artifact SHA-256: `83fc039473c5595ace860d8c4fafa220ff474b3fc6bfdb4293327f1a37e94d86`.
- Upstream license expression: `MIT OR Apache-2.0`.
- Distribution selection: MIT.
- [Versioned source listing](https://docs.rs/crate/serde_json/1.0.149/source/).
- [LICENSE-APACHE](https://docs.rs/crate/serde_json/1.0.149/source/LICENSE-APACHE) — text SHA-256 `62c7a1e35f56406896d7aa7ca52d0cc0d272ac022b5d2796e7d6905db8a3636a`.
- [LICENSE-MIT](https://docs.rs/crate/serde_json/1.0.149/source/LICENSE-MIT) — text SHA-256 `23f18e03dc49df91622fe2a76176497404e46ced8a715d9d2b67a7446571cca3`.

### smallvec 1.15.1

- Registry artifact SHA-256: `67b1b7a3b5fe4f1376887184045fcf45c69e92af734b7aaddc05fb777b6fbd03`.
- Upstream license expression: `MIT OR Apache-2.0`.
- Distribution selection: MIT.
- [Versioned source listing](https://docs.rs/crate/smallvec/1.15.1/source/).
- [LICENSE-APACHE](https://docs.rs/crate/smallvec/1.15.1/source/LICENSE-APACHE) — text SHA-256 `a60eea817514531668d7e00765731449fe14d059d3249e0bc93b36de45f759f2`.
- [LICENSE-MIT](https://docs.rs/crate/smallvec/1.15.1/source/LICENSE-MIT) — text SHA-256 `0b28172679e0009b655da42797c03fd163a3379d5cfa67ba1f1655e974a2a1a9`.

### speedate 0.17.0

- Registry artifact SHA-256: `aba069c070b5e213f2a094deb7e5ed50ecb092be36102a4f4042e8d2056d060e`.
- Upstream license expression: `MIT`.
- Distribution selection: MIT.
- [Versioned source listing](https://docs.rs/crate/speedate/0.17.0/source/).
- [LICENSE](https://docs.rs/crate/speedate/0.17.0/source/LICENSE) — text SHA-256 `2afdd30d54b4d62b6f488a6bcc1546e84ec5061f13f4209c03d012348783795a`.

### stable_deref_trait 1.2.0

- Registry artifact SHA-256: `a8f112729512f8e442d81f95a8a7ddf2b7c6b8a1a6f509a95864142b30cab2d3`.
- Upstream license expression: `MIT OR Apache-2.0`.
- Distribution selection: MIT.
- [Versioned source listing](https://docs.rs/crate/stable_deref_trait/1.2.0/source/).
- [LICENSE-APACHE](https://docs.rs/crate/stable_deref_trait/1.2.0/source/LICENSE-APACHE) — text SHA-256 `a60eea817514531668d7e00765731449fe14d059d3249e0bc93b36de45f759f2`.
- [LICENSE-MIT](https://docs.rs/crate/stable_deref_trait/1.2.0/source/LICENSE-MIT) — text SHA-256 `5e05b024f653a5ce199e77cbbbd42fb5553562ec714b819421ed0c3e552a75d7`.

### static_assertions 1.1.0

- Registry artifact SHA-256: `a2eb9349b6444b326872e140eb1cf5e7c522154d69e7a0ffb0fb81c06b37543f`.
- Upstream license expression: `MIT OR Apache-2.0`.
- Distribution selection: MIT.
- [Versioned source listing](https://docs.rs/crate/static_assertions/1.1.0/source/).
- [LICENSE-APACHE](https://docs.rs/crate/static_assertions/1.1.0/source/LICENSE-APACHE) — text SHA-256 `cfc7749b96f63bd31c3c42b5c471bf756814053e847c10f3eb003417bc523d30`.
- [LICENSE-MIT](https://docs.rs/crate/static_assertions/1.1.0/source/LICENSE-MIT) — text SHA-256 `ea084a2373ebc1f0902c09266e7bf25a05ab3814c1805bb017ffa7308f90c061`.

### strum 0.27.2

- Registry artifact SHA-256: `af23d6f6c1a224baef9d3f61e287d2761385a5b88fdab4eb4c6f11aeb54c4bcf`.
- Upstream license expression: `MIT`.
- Distribution selection: MIT.
- [Versioned source listing](https://docs.rs/crate/strum/0.27.2/source/).
- [LICENSE](https://docs.rs/crate/strum/0.27.2/source/LICENSE) — text SHA-256 `8bce3b45e49ecd1461f223b46de133d8f62cd39f745cfdaf81bee554b908bd42`.

### strum_macros 0.27.2

- Registry artifact SHA-256: `7695ce3845ea4b33927c055a39dc438a45b059f7c1b3d91d38d10355fb8cbca7`.
- Upstream license expression: `MIT`.
- Distribution selection: MIT.
- [Versioned source listing](https://docs.rs/crate/strum_macros/0.27.2/source/).
- [LICENSE](https://docs.rs/crate/strum_macros/0.27.2/source/LICENSE) — text SHA-256 `8bce3b45e49ecd1461f223b46de133d8f62cd39f745cfdaf81bee554b908bd42`.

### syn 2.0.82

- Registry artifact SHA-256: `83540f837a8afc019423a8edb95b52a8effe46957ee402287f4292fae35be021`.
- Upstream license expression: `MIT OR Apache-2.0`.
- Distribution selection: MIT.
- [Versioned source listing](https://docs.rs/crate/syn/2.0.82/source/).
- [LICENSE-APACHE](https://docs.rs/crate/syn/2.0.82/source/LICENSE-APACHE) — text SHA-256 `62c7a1e35f56406896d7aa7ca52d0cc0d272ac022b5d2796e7d6905db8a3636a`.
- [LICENSE-MIT](https://docs.rs/crate/syn/2.0.82/source/LICENSE-MIT) — text SHA-256 `23f18e03dc49df91622fe2a76176497404e46ced8a715d9d2b67a7446571cca3`.

### synstructure 0.13.1

- Registry artifact SHA-256: `c8af7666ab7b6390ab78131fb5b0fce11d6b7a6951602017c35fa82800708971`.
- Upstream license expression: `MIT`.
- Distribution selection: MIT.
- [Versioned source listing](https://docs.rs/crate/synstructure/0.13.1/source/).
- [LICENSE](https://docs.rs/crate/synstructure/0.13.1/source/LICENSE) — text SHA-256 `219920e865eee70b7dcfc948a86b099e7f4fe2de01bcca2ca9a20c0a033f2b59`.

### tap 1.0.1

- Registry artifact SHA-256: `55937e1799185b12863d447f42597ed69d9928686b8d88a1df17376a097d8369`.
- Upstream license expression: `MIT`.
- Distribution selection: MIT.
- [Versioned source listing](https://docs.rs/crate/tap/1.0.1/source/).
- [LICENSE.txt](https://docs.rs/crate/tap/1.0.1/source/LICENSE.txt) — text SHA-256 `8b4e95f5cf0dc40269c99f7b787203ffe04ded245ca2427422c196efa2b2f42a`.

### target-lexicon 0.13.4

- Registry artifact SHA-256: `b1dd07eb858a2067e2f3c7155d54e929265c264e6f37efe3ee7a8d1b5a1dd0ba`.
- Upstream license expression: `Apache-2.0 WITH LLVM-exception`.
- Distribution selection: Apache-2.0 WITH LLVM-exception.
- [Versioned source listing](https://docs.rs/crate/target-lexicon/0.13.4/source/).
- [LICENSE](https://docs.rs/crate/target-lexicon/0.13.4/source/LICENSE) — text SHA-256 `268872b9816f90fd8e85db5a28d33f8150ebb8dd016653fb39ef1f94f2686bc5`.

### tinystr 0.7.6

- Registry artifact SHA-256: `9117f5d4db391c1cf6927e7bea3db74b9a1c1add8f7eda9ffd5364f40f57b82f`.
- Upstream license expression: `Unicode-3.0`.
- Distribution selection: Unicode-3.0.
- [Versioned source listing](https://docs.rs/crate/tinystr/0.7.6/source/).
- [LICENSE](https://docs.rs/crate/tinystr/0.7.6/source/LICENSE) — text SHA-256 `f367c1b8e1aa262435251e442901da4607b4650e0e63a026f5044473ecfb90f2`.

### unicode-ident 1.0.12

- Registry artifact SHA-256: `3354b9ac3fae1ff6755cb6db53683adb661634f67557942dea4facebec0fee4b`.
- Upstream license expression: `(MIT OR Apache-2.0) AND Unicode-DFS-2016`.
- Distribution selection: MIT AND Unicode-DFS-2016.
- [Versioned source listing](https://docs.rs/crate/unicode-ident/1.0.12/source/).
- [LICENSE-APACHE](https://docs.rs/crate/unicode-ident/1.0.12/source/LICENSE-APACHE) — text SHA-256 `62c7a1e35f56406896d7aa7ca52d0cc0d272ac022b5d2796e7d6905db8a3636a`.
- [LICENSE-MIT](https://docs.rs/crate/unicode-ident/1.0.12/source/LICENSE-MIT) — text SHA-256 `23f18e03dc49df91622fe2a76176497404e46ced8a715d9d2b67a7446571cca3`.
- [LICENSE-UNICODE](https://docs.rs/crate/unicode-ident/1.0.12/source/LICENSE-UNICODE) — text SHA-256 `68f5b9f5ea36881a0942ba02f558e9e1faf76cc09cb165ad801744c61b738844`.

### url 2.5.8

- Registry artifact SHA-256: `ff67a8a4397373c3ef660812acab3268222035010ab8680ec4215f38ba3d0eed`.
- Upstream license expression: `MIT OR Apache-2.0`.
- Distribution selection: MIT.
- [Versioned source listing](https://docs.rs/crate/url/2.5.8/source/).
- [LICENSE-APACHE](https://docs.rs/crate/url/2.5.8/source/LICENSE-APACHE) — text SHA-256 `a60eea817514531668d7e00765731449fe14d059d3249e0bc93b36de45f759f2`.
- [LICENSE-MIT](https://docs.rs/crate/url/2.5.8/source/LICENSE-MIT) — text SHA-256 `b38f11f6096706e6de553dabe2a7ed142d59b6fa8c97e290c67496154745cdd5`.

### utf16_iter 1.0.5

- Registry artifact SHA-256: `c8232dd3cdaed5356e0f716d285e4b40b932ac434100fe9b7e0e8e935b9e6246`.
- Upstream license expression: `Apache-2.0 OR MIT`.
- Distribution selection: MIT.
- [Versioned source listing](https://docs.rs/crate/utf16_iter/1.0.5/source/).
- [COPYRIGHT](https://docs.rs/crate/utf16_iter/1.0.5/source/COPYRIGHT) — text SHA-256 `b84efe109a420fa3ca98be33f4227327af7ffa426195812c270feb1268bc2426`.
- [LICENSE-APACHE](https://docs.rs/crate/utf16_iter/1.0.5/source/LICENSE-APACHE) — text SHA-256 `cfc7749b96f63bd31c3c42b5c471bf756814053e847c10f3eb003417bc523d30`.
- [LICENSE-MIT](https://docs.rs/crate/utf16_iter/1.0.5/source/LICENSE-MIT) — text SHA-256 `3fa4ca83dcc9237839b1bdeb2e6d16bdfb5ec0c5ce42b24694d8bbf0dcbef72c`.

### utf8_iter 1.0.4

- Registry artifact SHA-256: `b6c140620e7ffbb22c2dee59cafe6084a59b5ffc27a8859a5f0d494b5d52b6be`.
- Upstream license expression: `Apache-2.0 OR MIT`.
- Distribution selection: MIT.
- [Versioned source listing](https://docs.rs/crate/utf8_iter/1.0.4/source/).
- [COPYRIGHT](https://docs.rs/crate/utf8_iter/1.0.4/source/COPYRIGHT) — text SHA-256 `c30152c94a6d75e021adbc52b3a52470366a46edb917e17deae3259251af244c`.
- [LICENSE-APACHE](https://docs.rs/crate/utf8_iter/1.0.4/source/LICENSE-APACHE) — text SHA-256 `cfc7749b96f63bd31c3c42b5c471bf756814053e847c10f3eb003417bc523d30`.
- [LICENSE-MIT](https://docs.rs/crate/utf8_iter/1.0.4/source/LICENSE-MIT) — text SHA-256 `3fa4ca83dcc9237839b1bdeb2e6d16bdfb5ec0c5ce42b24694d8bbf0dcbef72c`.

### uuid 1.23.0

- Registry artifact SHA-256: `5ac8b6f42ead25368cf5b098aeb3dc8a1a2c05a3eee8a9a1a68c640edbfc79d9`.
- Upstream license expression: `Apache-2.0 OR MIT`.
- Distribution selection: MIT.
- [Versioned source listing](https://docs.rs/crate/uuid/1.23.0/source/).
- [LICENSE-APACHE](https://docs.rs/crate/uuid/1.23.0/source/LICENSE-APACHE) — text SHA-256 `a60eea817514531668d7e00765731449fe14d059d3249e0bc93b36de45f759f2`.
- [LICENSE-MIT](https://docs.rs/crate/uuid/1.23.0/source/LICENSE-MIT) — text SHA-256 `436bc5a105d8e57dcd8778730f3754f7bf39c14d2f530e4cde4bd2d17a83ec3d`.

### version_check 0.9.5

- Registry artifact SHA-256: `0b928f33d975fc6ad9f86c8f283853ad26bdd5b10b7f1542aa2fa15e2289105a`.
- Upstream license expression: `MIT OR Apache-2.0`.
- Distribution selection: MIT.
- [Versioned source listing](https://docs.rs/crate/version_check/0.9.5/source/).
- [LICENSE-APACHE](https://docs.rs/crate/version_check/0.9.5/source/LICENSE-APACHE) — text SHA-256 `a60eea817514531668d7e00765731449fe14d059d3249e0bc93b36de45f759f2`.
- [LICENSE-MIT](https://docs.rs/crate/version_check/0.9.5/source/LICENSE-MIT) — text SHA-256 `b7e650f3fce5c53249d1cdc608b54df156a97edd636cf9d23498d0cfe7aec63e`.

### wasi 0.14.2+wasi-0.2.4

- Registry artifact SHA-256: `9683f9a5a998d873c0d21fcbe3c083009670149a8fab228644b8bd36b2c48cb3`.
- Upstream license expression: `Apache-2.0 WITH LLVM-exception OR Apache-2.0 OR MIT`.
- Distribution selection: MIT.
- [Versioned source listing](https://docs.rs/crate/wasi/0.14.2+wasi-0.2.4/source/).
- [LICENSE-APACHE](https://docs.rs/crate/wasi/0.14.2+wasi-0.2.4/source/LICENSE-APACHE) — text SHA-256 `a60eea817514531668d7e00765731449fe14d059d3249e0bc93b36de45f759f2`.
- [LICENSE-Apache-2.0_WITH_LLVM-exception](https://docs.rs/crate/wasi/0.14.2+wasi-0.2.4/source/LICENSE-Apache-2.0_WITH_LLVM-exception) — text SHA-256 `268872b9816f90fd8e85db5a28d33f8150ebb8dd016653fb39ef1f94f2686bc5`.
- [LICENSE-MIT](https://docs.rs/crate/wasi/0.14.2+wasi-0.2.4/source/LICENSE-MIT) — text SHA-256 `23f18e03dc49df91622fe2a76176497404e46ced8a715d9d2b67a7446571cca3`.

### wasm-bindgen 0.2.100

- Registry artifact SHA-256: `1edc8929d7499fc4e8f0be2262a241556cfc54a0bea223790e71446f2aab1ef5`.
- Upstream license expression: `MIT OR Apache-2.0`.
- Distribution selection: MIT.
- [Versioned source listing](https://docs.rs/crate/wasm-bindgen/0.2.100/source/).
- [LICENSE-APACHE](https://docs.rs/crate/wasm-bindgen/0.2.100/source/LICENSE-APACHE) — text SHA-256 `a60eea817514531668d7e00765731449fe14d059d3249e0bc93b36de45f759f2`.
- [LICENSE-MIT](https://docs.rs/crate/wasm-bindgen/0.2.100/source/LICENSE-MIT) — text SHA-256 `378f5840b258e2779c39418f3f2d7b2ba96f1c7917dd6be0713f88305dbda397`.

### wasm-bindgen-backend 0.2.100

- Registry artifact SHA-256: `2f0a0651a5c2bc21487bde11ee802ccaf4c51935d0d3d42a6101f98161700bc6`.
- Upstream license expression: `MIT OR Apache-2.0`.
- Distribution selection: MIT.
- [Versioned source listing](https://docs.rs/crate/wasm-bindgen-backend/0.2.100/source/).
- [LICENSE-APACHE](https://docs.rs/crate/wasm-bindgen-backend/0.2.100/source/LICENSE-APACHE) — text SHA-256 `a60eea817514531668d7e00765731449fe14d059d3249e0bc93b36de45f759f2`.
- [LICENSE-MIT](https://docs.rs/crate/wasm-bindgen-backend/0.2.100/source/LICENSE-MIT) — text SHA-256 `378f5840b258e2779c39418f3f2d7b2ba96f1c7917dd6be0713f88305dbda397`.

### wasm-bindgen-macro 0.2.100

- Registry artifact SHA-256: `7fe63fc6d09ed3792bd0897b314f53de8e16568c2b3f7982f468c0bf9bd0b407`.
- Upstream license expression: `MIT OR Apache-2.0`.
- Distribution selection: MIT.
- [Versioned source listing](https://docs.rs/crate/wasm-bindgen-macro/0.2.100/source/).
- [LICENSE-APACHE](https://docs.rs/crate/wasm-bindgen-macro/0.2.100/source/LICENSE-APACHE) — text SHA-256 `a60eea817514531668d7e00765731449fe14d059d3249e0bc93b36de45f759f2`.
- [LICENSE-MIT](https://docs.rs/crate/wasm-bindgen-macro/0.2.100/source/LICENSE-MIT) — text SHA-256 `378f5840b258e2779c39418f3f2d7b2ba96f1c7917dd6be0713f88305dbda397`.

### wasm-bindgen-macro-support 0.2.100

- Registry artifact SHA-256: `8ae87ea40c9f689fc23f209965b6fb8a99ad69aeeb0231408be24920604395de`.
- Upstream license expression: `MIT OR Apache-2.0`.
- Distribution selection: MIT.
- [Versioned source listing](https://docs.rs/crate/wasm-bindgen-macro-support/0.2.100/source/).
- [LICENSE-APACHE](https://docs.rs/crate/wasm-bindgen-macro-support/0.2.100/source/LICENSE-APACHE) — text SHA-256 `a60eea817514531668d7e00765731449fe14d059d3249e0bc93b36de45f759f2`.
- [LICENSE-MIT](https://docs.rs/crate/wasm-bindgen-macro-support/0.2.100/source/LICENSE-MIT) — text SHA-256 `378f5840b258e2779c39418f3f2d7b2ba96f1c7917dd6be0713f88305dbda397`.

### wasm-bindgen-shared 0.2.100

- Registry artifact SHA-256: `1a05d73b933a847d6cccdda8f838a22ff101ad9bf93e33684f39c1f5f0eece3d`.
- Upstream license expression: `MIT OR Apache-2.0`.
- Distribution selection: MIT.
- [Versioned source listing](https://docs.rs/crate/wasm-bindgen-shared/0.2.100/source/).
- [LICENSE-APACHE](https://docs.rs/crate/wasm-bindgen-shared/0.2.100/source/LICENSE-APACHE) — text SHA-256 `a60eea817514531668d7e00765731449fe14d059d3249e0bc93b36de45f759f2`.
- [LICENSE-MIT](https://docs.rs/crate/wasm-bindgen-shared/0.2.100/source/LICENSE-MIT) — text SHA-256 `378f5840b258e2779c39418f3f2d7b2ba96f1c7917dd6be0713f88305dbda397`.

### wit-bindgen-rt 0.39.0

- Registry artifact SHA-256: `6f42320e61fe2cfd34354ecb597f86f413484a798ba44a8ca1165c58d42da6c1`.
- Upstream license expression: `Apache-2.0 WITH LLVM-exception OR Apache-2.0 OR MIT`.
- Distribution selection: MIT.
- [Versioned source listing](https://docs.rs/crate/wit-bindgen-rt/0.39.0/source/).
- Notice source commit: `f2393e6e98fa5f9236cac580db8a3fc9de6a4b70`.
- [LICENSE-MIT](https://raw.githubusercontent.com/bytecodealliance/wit-bindgen/f2393e6e98fa5f9236cac580db8a3fc9de6a4b70/LICENSE-MIT) — text SHA-256 `23f18e03dc49df91622fe2a76176497404e46ced8a715d9d2b67a7446571cca3`.

### write16 1.0.0

- Registry artifact SHA-256: `d1890f4022759daae28ed4fe62859b1236caebfc61ede2f63ed4e695f3f6d936`.
- Upstream license expression: `Apache-2.0 OR MIT`.
- Distribution selection: MIT.
- [Versioned source listing](https://docs.rs/crate/write16/1.0.0/source/).
- [COPYRIGHT](https://docs.rs/crate/write16/1.0.0/source/COPYRIGHT) — text SHA-256 `3210be7332b5bdf48eb24a945258b9f38616a2cceb0dfc06e3c3c7e9740475a0`.
- [LICENSE-APACHE](https://docs.rs/crate/write16/1.0.0/source/LICENSE-APACHE) — text SHA-256 `cfc7749b96f63bd31c3c42b5c471bf756814053e847c10f3eb003417bc523d30`.
- [LICENSE-MIT](https://docs.rs/crate/write16/1.0.0/source/LICENSE-MIT) — text SHA-256 `3fa4ca83dcc9237839b1bdeb2e6d16bdfb5ec0c5ce42b24694d8bbf0dcbef72c`.

### writeable 0.5.5

- Registry artifact SHA-256: `1e9df38ee2d2c3c5948ea468a8406ff0db0b29ae1ffde1bcf20ef305bcc95c51`.
- Upstream license expression: `Unicode-3.0`.
- Distribution selection: Unicode-3.0.
- [Versioned source listing](https://docs.rs/crate/writeable/0.5.5/source/).
- [LICENSE](https://docs.rs/crate/writeable/0.5.5/source/LICENSE) — text SHA-256 `f367c1b8e1aa262435251e442901da4607b4650e0e63a026f5044473ecfb90f2`.

### wyz 0.5.1

- Registry artifact SHA-256: `05f360fc0b24296329c78fda852a1e9ae82de9cf7b27dae4b7f62f118f77b9ed`.
- Upstream license expression: `MIT`.
- Distribution selection: MIT.
- [Versioned source listing](https://docs.rs/crate/wyz/0.5.1/source/).
- [LICENSE.txt](https://docs.rs/crate/wyz/0.5.1/source/LICENSE.txt) — text SHA-256 `411781fd38700f2357a14126d0ab048164ab881f1dcb335c1bb932e232c9a2f5`.

### yoke 0.7.4

- Registry artifact SHA-256: `6c5b1314b079b0930c31e3af543d8ee1757b1951ae1e1565ec704403a7240ca5`.
- Upstream license expression: `Unicode-3.0`.
- Distribution selection: Unicode-3.0.
- [Versioned source listing](https://docs.rs/crate/yoke/0.7.4/source/).
- [LICENSE](https://docs.rs/crate/yoke/0.7.4/source/LICENSE) — text SHA-256 `f367c1b8e1aa262435251e442901da4607b4650e0e63a026f5044473ecfb90f2`.

### yoke-derive 0.7.4

- Registry artifact SHA-256: `28cc31741b18cb6f1d5ff12f5b7523e3d6eb0852bbbad19d73905511d9849b95`.
- Upstream license expression: `Unicode-3.0`.
- Distribution selection: Unicode-3.0.
- [Versioned source listing](https://docs.rs/crate/yoke-derive/0.7.4/source/).
- [LICENSE](https://docs.rs/crate/yoke-derive/0.7.4/source/LICENSE) — text SHA-256 `f367c1b8e1aa262435251e442901da4607b4650e0e63a026f5044473ecfb90f2`.

### zerocopy 0.8.25

- Registry artifact SHA-256: `a1702d9583232ddb9174e01bb7c15a2ab8fb1bc6f227aa1233858c351a3ba0cb`.
- Upstream license expression: `BSD-2-Clause OR Apache-2.0 OR MIT`.
- Distribution selection: MIT.
- [Versioned source listing](https://docs.rs/crate/zerocopy/0.8.25/source/).
- [LICENSE-APACHE](https://docs.rs/crate/zerocopy/0.8.25/source/LICENSE-APACHE) — text SHA-256 `9d185ac6703c4b0453974c0d85e9eee43e6941009296bb1f5eb0b54e2329e9f3`.
- [LICENSE-BSD](https://docs.rs/crate/zerocopy/0.8.25/source/LICENSE-BSD) — text SHA-256 `83c1763356e822adde0a2cae748d938a73fdc263849ccff6b27776dff213bd32`.
- [LICENSE-MIT](https://docs.rs/crate/zerocopy/0.8.25/source/LICENSE-MIT) — text SHA-256 `1a2f5c12ddc934d58956aa5dbdd3255fe55fd957633ab7d0d39e4f0daa73f7df`.

### zerocopy-derive 0.8.25

- Registry artifact SHA-256: `28a6e20d751156648aa063f3800b706ee209a32c0b4d9f24be3d980b01be55ef`.
- Upstream license expression: `BSD-2-Clause OR Apache-2.0 OR MIT`.
- Distribution selection: MIT.
- [Versioned source listing](https://docs.rs/crate/zerocopy-derive/0.8.25/source/).
- [LICENSE-APACHE](https://docs.rs/crate/zerocopy-derive/0.8.25/source/LICENSE-APACHE) — text SHA-256 `9d185ac6703c4b0453974c0d85e9eee43e6941009296bb1f5eb0b54e2329e9f3`.
- [LICENSE-BSD](https://docs.rs/crate/zerocopy-derive/0.8.25/source/LICENSE-BSD) — text SHA-256 `83c1763356e822adde0a2cae748d938a73fdc263849ccff6b27776dff213bd32`.
- [LICENSE-MIT](https://docs.rs/crate/zerocopy-derive/0.8.25/source/LICENSE-MIT) — text SHA-256 `1a2f5c12ddc934d58956aa5dbdd3255fe55fd957633ab7d0d39e4f0daa73f7df`.

### zerofrom 0.1.4

- Registry artifact SHA-256: `91ec111ce797d0e0784a1116d0ddcdbea84322cd79e5d5ad173daeba4f93ab55`.
- Upstream license expression: `Unicode-3.0`.
- Distribution selection: Unicode-3.0.
- [Versioned source listing](https://docs.rs/crate/zerofrom/0.1.4/source/).
- [LICENSE](https://docs.rs/crate/zerofrom/0.1.4/source/LICENSE) — text SHA-256 `f367c1b8e1aa262435251e442901da4607b4650e0e63a026f5044473ecfb90f2`.

### zerofrom-derive 0.1.4

- Registry artifact SHA-256: `0ea7b4a3637ea8669cedf0f1fd5c286a17f3de97b8dd5a70a6c167a1730e63a5`.
- Upstream license expression: `Unicode-3.0`.
- Distribution selection: Unicode-3.0.
- [Versioned source listing](https://docs.rs/crate/zerofrom-derive/0.1.4/source/).
- [LICENSE](https://docs.rs/crate/zerofrom-derive/0.1.4/source/LICENSE) — text SHA-256 `f367c1b8e1aa262435251e442901da4607b4650e0e63a026f5044473ecfb90f2`.

### zerovec 0.10.4

- Registry artifact SHA-256: `aa2b893d79df23bfb12d5461018d408ea19dfafe76c2c7ef6d4eba614f8ff079`.
- Upstream license expression: `Unicode-3.0`.
- Distribution selection: Unicode-3.0.
- [Versioned source listing](https://docs.rs/crate/zerovec/0.10.4/source/).
- [LICENSE](https://docs.rs/crate/zerovec/0.10.4/source/LICENSE) — text SHA-256 `f367c1b8e1aa262435251e442901da4607b4650e0e63a026f5044473ecfb90f2`.

### zerovec-derive 0.10.3

- Registry artifact SHA-256: `6eafa6dfb17584ea3e2bd6e76e0cc15ad7af12b09abdd1ca55961bed9b1063c6`.
- Upstream license expression: `Unicode-3.0`.
- Distribution selection: Unicode-3.0.
- [Versioned source listing](https://docs.rs/crate/zerovec-derive/0.10.3/source/).
- [LICENSE](https://docs.rs/crate/zerovec-derive/0.10.3/source/LICENSE) — text SHA-256 `f367c1b8e1aa262435251e442901da4607b4650e0e63a026f5044473ecfb90f2`.

### zmij 1.0.6

- Registry artifact SHA-256: `aac060176f7020d62c3bcc1cdbcec619d54f48b07ad1963a3f80ce7a0c17755f`.
- Upstream license expression: `MIT`.
- Distribution selection: MIT.
- [Versioned source listing](https://docs.rs/crate/zmij/1.0.6/source/).
- [LICENSE-MIT](https://docs.rs/crate/zmij/1.0.6/source/LICENSE-MIT) — text SHA-256 `23f18e03dc49df91622fe2a76176497404e46ced8a715d9d2b67a7446571cca3`.
