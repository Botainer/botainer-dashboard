# Rust standard-library attribution

The pinned pydantic-core native extension contains Rust standard-library code.
[rust-stdlib-NOTICES.txt](rust-stdlib-NOTICES.txt) supplies the associated
license texts and copyright notices alongside the separate pydantic-core crate
notices. The original dependency wheel is unchanged.

## Exact artifact

| Item | Value |
| --- | --- |
| Wheel | `pydantic_core-2.46.5-cp313-cp313-macosx_11_0_arm64.whl` |
| Wheel SHA-256 | `f332f0e72a5a0400141f830744e141bf9f97917878dbe968669e8a7fefea78ff` |
| Native member | `pydantic_core/_pydantic_core.cpython-313-darwin.so` |
| Member SHA-256 | `e040625fdb57a9edb69c9a2351703c72061a9888d8ad8e874d696ba448c83c7f` |
| Embedded compiler source commit | `88d9e12ae178fab0fb5cc050a94da85685d449ea` |
| Compiler version from [src/version](https://raw.githubusercontent.com/rust-lang/rust/88d9e12ae178fab0fb5cc050a94da85685d449ea/src/version) | Rust 1.98.0 |
| Companion notice SHA-256 | `f81447b932b65f4ddfe022ef78351345f9f7c0966ea26813403923e24434fca4` |

This review applies to that artifact. A different dependency version, wheel,
platform or compiler requires a renewed notice review; this file does not
qualify other pydantic-core builds.

## Collection scope

The collection combines the exact compiler commit's Rust MIT/Apache licenses,
the library copyright entries in
[license-metadata.json](https://raw.githubusercontent.com/rust-lang/rust/88d9e12ae178fab0fb5cc050a94da85685d449ea/license-metadata.json),
the Unicode license, explicit stdarch/portable-simd/compiler-builtins/libm notices, and
notices for all 30 external packages in
[library/Cargo.lock](https://raw.githubusercontent.com/rust-lang/rust/88d9e12ae178fab0fb5cc050a94da85685d449ea/library/Cargo.lock).
It also retains 23 distinct copyright/license comment excerpts from 73
compiler-builtins/libm runtime source files. Identical excerpts are listed
once with their source-file mapping. Permission text is retained verbatim;
the two excerpts followed by unrelated algorithm documentation stop at the
end of the license comment.

The package list is intentionally broader than the linked macOS code: it
includes optional other-target packages and build/test dependencies. For
example, fortanix-sgx-abi is specific to SGX and vex-sdk to VEX targets.
Their notices do not establish that their code is in this wheel. The LGPL
alternative offered by r-efi/r-efi-alloc is not selected here: their supplied
AUTHORS files include their full MIT permission and copyright notices.

This is **not** a byte-for-byte reproduction of Rust's generated
`COPYRIGHT-library.html`, and Rust was not rebuilt. The official
[generator](https://raw.githubusercontent.com/rust-lang/rust/88d9e12ae178fab0fb5cc050a94da85685d449ea/src/tools/generate-copyright/src/main.rs) also considers
the separate stdarch tooling workspace. Its 122 external test/generator/example
lock entries are excluded here: [core includes core_arch source directly](https://raw.githubusercontent.com/rust-lang/rust/88d9e12ae178fab0fb5cc050a94da85685d449ea/library/core/src/lib.rs),
and [core_arch's manifest](https://raw.githubusercontent.com/rust-lang/rust/88d9e12ae178fab0fb5cc050a94da85685d449ea/library/stdarch/crates/core_arch/Cargo.toml)
has only development dependencies. The stdarch source licenses are included.
The generated file was not available at the checked versioned documentation URL.

Rust's blanket in-tree REUSE annotation is supplemented by the more specific
[compiler-builtins license](https://raw.githubusercontent.com/rust-lang/rust/88d9e12ae178fab0fb5cc050a94da85685d449ea/library/compiler-builtins/LICENSE.txt)
(MIT AND Apache-2.0 WITH LLVM-exception) and its bundled libm notices.
This avoids treating the blanket MIT/Apache annotation as the entire notice
set. No Rust compiler, LLVM toolchain, operating-system library or source
archive is distributed by adding this notice collection.

## Provenance and limits

Crate notice texts were read from fixed-version docs.rs source-documentation
pages. Their versions and registry checksums come from the exact Rust lockfile;
the checksum column identifies upstream locked artifacts, not newly downloaded
or independently checked crate archives. No crate archive was downloaded for
this review. Text SHA-256 values below identify the decoded notice text, not
the HTML page bytes.

Three packages had no top-level notice in their documentation listing. Their
declared `.cargo_vcs_info.json` records provided the source commits used for
the supplemental repository-root license files:

| Package | Recorded commit | Supplement |
| --- | --- | --- |
| fortanix-sgx-abi 0.6.1 | `adcc6cdb981bcf2101437edbd3ef6144246621d3` | Full MPL-2.0 text |
| vex-sdk 0.27.1 | `6c0b83d35b6fc31a7607efe456e387df45476314` | MIT text and vexide copyright |
| wasip1 1.0.0 | `e55b3b02fcda2a016f5df14573ff05c5b1e313d6` | MIT, Apache-2.0 and LLVM-exception texts |

The vex-sdk record also says `dirty: true`; the referenced repository notice
is preserved, but an exact match between that commit and its published crate
is not asserted. It is an optional non-macOS entry in this notice superset.
Compiler-builtins/libm source-page bytes were checked against their Git blob
identities at the compiler commit before extracting comments. This is an
attribution review with bounded provenance, not a legal certification or an
exact build/link reconstruction.

## Locked external packages

License expressions below reproduce package metadata. Slash-separated older
expressions are retained as supplied. This collection selects **MIT wherever
the component offers it as a permitted alternative**. That choice does not
remove mandatory `AND` terms: compiler-builtins retains MIT AND Apache-2.0
WITH LLVM-exception, and the relevant Unicode and BSD terms also remain.
Components without an MIT alternative retain their stated terms, including
foldhash's Zlib license and the optional SGX entry's MPL-2.0 license. Complete
upstream alternative license texts are preserved even when MIT is selected.

| Package | License expression | Locked registry SHA-256 |
| --- | --- | --- |
| [addr2line 0.25.1](https://docs.rs/crate/addr2line/0.25.1/source/) | `Apache-2.0 OR MIT` | `1b5d307320b3181d6d7954e663bd7c774a838b8220fe0593c86d9fb09f498b4b` |
| [adler2 2.0.1](https://docs.rs/crate/adler2/2.0.1/source/) | `0BSD OR MIT OR Apache-2.0` | `320119579fcad9c21884f5c4861d16174d0e06250625266f50fe6898340abefa` |
| [cc 1.2.0](https://docs.rs/crate/cc/1.2.0/source/) | `MIT OR Apache-2.0` | `1aeb932158bd710538c73702db6945cb68a8fb08c519e6e12706b94263b36db8` |
| [cfg-if 1.0.4](https://docs.rs/crate/cfg-if/1.0.4/source/) | `MIT OR Apache-2.0` | `9330f8b2ff13f34540b44e946ef35111825727b38d33286ef986142615121801` |
| [dlmalloc 0.2.13](https://docs.rs/crate/dlmalloc/0.2.13/source/) | `MIT/Apache-2.0` | `9f5b01c17f85ee988d832c40e549a64bd89ab2c9f8d8a613bdf5122ae507e294` |
| [foldhash 0.2.0](https://docs.rs/crate/foldhash/0.2.0/source/) | `Zlib` | `77ce24cb58228fbb8aa041425bb1050850ac19177686ea6e0f41a70416f56fdb` |
| [fortanix-sgx-abi 0.6.1](https://docs.rs/crate/fortanix-sgx-abi/0.6.1/source/) | `MPL-2.0` | `5efc85edd5b83e8394f4371dd0da6859dff63dd387dab8568fece6af4cde6f84` |
| [getopts 0.2.24](https://docs.rs/crate/getopts/0.2.24/source/) | `MIT OR Apache-2.0` | `cfe4fbac503b8d1f88e6676011885f34b7174f46e59956bba534ba83abded4df` |
| [gimli 0.32.3](https://docs.rs/crate/gimli/0.32.3/source/) | `MIT OR Apache-2.0` | `e629b9b98ef3dd8afe6ca2bd0f89306cec16d43d907889945bc5d6687f2f13c7` |
| [hashbrown 0.17.1](https://docs.rs/crate/hashbrown/0.17.1/source/) | `MIT OR Apache-2.0` | `ed5909b6e89a2db4456e54cd5f673791d7eca6732202bbf2a9cc504fe2f9b84a` |
| [hermit-abi 0.5.2](https://docs.rs/crate/hermit-abi/0.5.2/source/) | `MIT OR Apache-2.0` | `fc0fef456e4baa96da950455cd02c081ca953b141298e41db3fc7e36b1da849c` |
| [libc 0.2.185](https://docs.rs/crate/libc/0.2.185/source/) | `MIT OR Apache-2.0` | `52ff2c0fe9bc6cb6b14a0592c2ff4fa9ceb83eea9db979b0487cd054946a2b8f` |
| [memchr 2.7.6](https://docs.rs/crate/memchr/2.7.6/source/) | `Unlicense OR MIT` | `f52b00d39961fc5b2736ea853c9cc86238e165017a493d1d5c8eac6bdc4cc273` |
| [miniz_oxide 0.8.9](https://docs.rs/crate/miniz_oxide/0.8.9/source/) | `MIT OR Zlib OR Apache-2.0` | `1fa76a2c86f704bdb222d66965fb3d63269ce38518b83cb0575fca855ebb6316` |
| [moto-rt 0.16.0](https://docs.rs/crate/moto-rt/0.16.0/source/) | `MIT OR Apache-2.0` | `29aea9f7dfeb258e030a84e0ec38a9c2ec2063d4f45eb2db31445cfc40b3dba1` |
| [object 0.37.3](https://docs.rs/crate/object/0.37.3/source/) | `Apache-2.0 OR MIT` | `ff76201f031d8863c38aa7f905eca4f53abbfa15f609db4277d44cd8938f33fe` |
| [r-efi 5.3.0](https://docs.rs/crate/r-efi/5.3.0/source/) | `MIT OR Apache-2.0 OR LGPL-2.1-or-later` | `69cdb34c158ceb288df11e18b4bd39de994f6657d83847bdffdbd7f346754b0f` |
| [r-efi-alloc 2.1.0](https://docs.rs/crate/r-efi-alloc/2.1.0/source/) | `MIT OR Apache-2.0 OR LGPL-2.1-or-later` | `dc2f58ef3ca9bb0f9c44d9aa8537601bcd3df94cc9314a40178cadf7d4466354` |
| [rand 0.9.2](https://docs.rs/crate/rand/0.9.2/source/) | `MIT OR Apache-2.0` | `6db2770f06117d490610c7488547d543617b21bfa07796d7a12f6f1bd53850d1` |
| [rand_core 0.9.3](https://docs.rs/crate/rand_core/0.9.3/source/) | `MIT OR Apache-2.0` | `99d9a13982dcf210057a8a78572b2217b667c3beacbf3a0d8b454f6f82837d38` |
| [rand_xorshift 0.4.0](https://docs.rs/crate/rand_xorshift/0.4.0/source/) | `MIT OR Apache-2.0` | `513962919efc330f829edb2535844d1b912b0fbe2ca165d613e4e8788bb05a5a` |
| [rustc-demangle 0.1.27](https://docs.rs/crate/rustc-demangle/0.1.27/source/) | `MIT/Apache-2.0` | `b50b8869d9fc858ce7266cce0194bd74df58b9d0e3f6df3a9fc8eb470d95c09d` |
| [rustc-literal-escaper 0.0.8](https://docs.rs/crate/rustc-literal-escaper/0.0.8/source/) | `Apache-2.0 OR MIT` | `bfe6f213fb658c8fb95baabd5420393438cf5a98d707f5dd701d9197c705f71e` |
| [shlex 1.3.0](https://docs.rs/crate/shlex/1.3.0/source/) | `MIT OR Apache-2.0` | `0fda2ff0d084019ba4d7c6f371c95d8fd75ce3524c3cb8fb653a3023f6323e64` |
| [unwinding 0.2.8](https://docs.rs/crate/unwinding/0.2.8/source/) | `MIT OR Apache-2.0` | `60612c845ef41699f39dc8c5391f252942c0a88b7d15da672eff0d14101bbd6d` |
| [vex-sdk 0.27.1](https://docs.rs/crate/vex-sdk/0.27.1/source/) | `MIT` | `79e5fe15afde1305478b35e2cb717fff59f485428534cf49cfdbfa4723379bf6` |
| [wasip1 1.0.0](https://docs.rs/crate/wasip1/1.0.0/source/) | `Apache-2.0 WITH LLVM-exception OR Apache-2.0 OR MIT` | `b5e26842486624357dbeb8f0381cf1fb42f022291fd787d4a816768fec8cc760` |
| [wasip2 1.0.3+wasi-0.2.9](https://docs.rs/crate/wasip2/1.0.3+wasi-0.2.9/source/) | `Apache-2.0 WITH LLVM-exception OR Apache-2.0 OR MIT` | `20064672db26d7cdc89c7798c48a0fdfac8213434a1186e5ef29fd560ae223d6` |
| [wasip3 0.6.0+wasi-0.3.0-rc-2026-03-15](https://docs.rs/crate/wasip3/0.6.0+wasi-0.3.0-rc-2026-03-15/source/) | `Apache-2.0 WITH LLVM-exception OR Apache-2.0 OR MIT` | `ed83456dd6a0b8581998c0365e4651fa2997e5093b49243b7f35391afaa7a3d9` |
| [wit-bindgen 0.57.1](https://docs.rs/crate/wit-bindgen/0.57.1/source/) | `Apache-2.0 WITH LLVM-exception OR Apache-2.0 OR MIT` | `1ebf944e87a7c253233ad6766e082e3cd714b5d03812acc24c318f549614536e` |

## Notice text ledger

Each numbered section is in the companion notice file. Full-file license texts
are retained unchanged; rows marked as comments are exact excerpts.

| Section | Source | Text SHA-256 |
| --- | --- | --- |
| 1 | [COPYRIGHT](https://raw.githubusercontent.com/rust-lang/rust/88d9e12ae178fab0fb5cc050a94da85685d449ea/COPYRIGHT) | `172020dbfd5b53a226dfde77616190a48dcff519b0bc0e6deb91a8450782c4af` |
| 2 | [LICENSE-MIT](https://raw.githubusercontent.com/rust-lang/rust/88d9e12ae178fab0fb5cc050a94da85685d449ea/LICENSE-MIT) | `b71bd43a069ca0641a9ecfe585ca7b3c53b5cc1608f8b68321168698e28b5ea1` |
| 3 | [LICENSE-APACHE](https://raw.githubusercontent.com/rust-lang/rust/88d9e12ae178fab0fb5cc050a94da85685d449ea/LICENSE-APACHE) | `62c7a1e35f56406896d7aa7ca52d0cc0d272ac022b5d2796e7d6905db8a3636a` |
| 4 | [LICENSES/Unicode-3.0.txt](https://raw.githubusercontent.com/rust-lang/rust/88d9e12ae178fab0fb5cc050a94da85685d449ea/LICENSES/Unicode-3.0.txt) | `f5062c9a188d81dfe66b56db4182dcf9e4b17c0d9b0d311a8e20b3a1b075c443` |
| 5 | [library/stdarch/LICENSE-MIT](https://raw.githubusercontent.com/rust-lang/rust/88d9e12ae178fab0fb5cc050a94da85685d449ea/library/stdarch/LICENSE-MIT) | `29662666b44dff84977b46e05642cdef910bc3a93a17b5fd86e632bafa59cf21` |
| 6 | [library/stdarch/LICENSE-APACHE](https://raw.githubusercontent.com/rust-lang/rust/88d9e12ae178fab0fb5cc050a94da85685d449ea/library/stdarch/LICENSE-APACHE) | `a60eea817514531668d7e00765731449fe14d059d3249e0bc93b36de45f759f2` |
| 7 | [library/portable-simd/LICENSE-MIT](https://raw.githubusercontent.com/rust-lang/rust/88d9e12ae178fab0fb5cc050a94da85685d449ea/library/portable-simd/LICENSE-MIT) | `eb07d497d26e6d68fbc76e793f5e5c9cfa197df2a580e47383569c287a55edf9` |
| 8 | [library/portable-simd/LICENSE-APACHE](https://raw.githubusercontent.com/rust-lang/rust/88d9e12ae178fab0fb5cc050a94da85685d449ea/library/portable-simd/LICENSE-APACHE) | `cfc7749b96f63bd31c3c42b5c471bf756814053e847c10f3eb003417bc523d30` |
| 9 | [library/compiler-builtins/LICENSE.txt](https://raw.githubusercontent.com/rust-lang/rust/88d9e12ae178fab0fb5cc050a94da85685d449ea/library/compiler-builtins/LICENSE.txt) | `ab6eec6caf0fa5775e411c7a8bc6a45c4ef2956b0980b157ab74fc5cd62a928b` |
| 10 | [library/compiler-builtins/libm/LICENSE.txt](https://raw.githubusercontent.com/rust-lang/rust/88d9e12ae178fab0fb5cc050a94da85685d449ea/library/compiler-builtins/libm/LICENSE.txt) | `3823dda7cf046602f4b4e77ec8e227863dc4736037cc85bb33d9f19febe16bb7` |
| 11 | [Fuchsia mutex copyright and permission comment](https://raw.githubusercontent.com/rust-lang/rust/88d9e12ae178fab0fb5cc050a94da85685d449ea/library/std/src/sys/sync/mutex/fuchsia.rs) | `de226c1188547e55d4c2c102bd03e4d29f884c3c09b41f64778ef051bbcb35e2` |
| 12 | [compiler-builtins/libm source notice 1](https://raw.githubusercontent.com/rust-lang/rust/88d9e12ae178fab0fb5cc050a94da85685d449ea/library/compiler-builtins/compiler-builtins/src/probestack.rs) | `a01e6ce9e0c0a2f9a09ff33c54f0b12dbbbd1c2a85231ce81731f7492ada99d4` |
| 13 | [compiler-builtins/libm source notice 2](https://raw.githubusercontent.com/rust-lang/rust/88d9e12ae178fab0fb5cc050a94da85685d449ea/library/compiler-builtins/libm/src/math/acos.rs) | `74344ebf347f27ca446e00f1c1921f705546f4b20e0fb31c183cd2c8464b1bc5` |
| 14 | [compiler-builtins/libm source notice 3](https://raw.githubusercontent.com/rust-lang/rust/88d9e12ae178fab0fb5cc050a94da85685d449ea/library/compiler-builtins/libm/src/math/acosf.rs) | `0c3d0381d2cd647be68590170490be5f624c962d9f95eb698489e578c1de8f05` |
| 15 | [compiler-builtins/libm source notice 4](https://raw.githubusercontent.com/rust-lang/rust/88d9e12ae178fab0fb5cc050a94da85685d449ea/library/compiler-builtins/libm/src/math/approx/cbrtf64.rs) | `11a1dc1ce0b6b2879da93a266c5aebe81c4324e749c77a2ce3048b5b24ab58c0` |
| 16 | [compiler-builtins/libm source notice 5](https://raw.githubusercontent.com/rust-lang/rust/88d9e12ae178fab0fb5cc050a94da85685d449ea/library/compiler-builtins/libm/src/math/atan2.rs) | `11f476f899dacfef801a097465ad0755ad4546afe03b02c9a17aa38e228344e1` |
| 17 | [compiler-builtins/libm source notice 6](https://raw.githubusercontent.com/rust-lang/rust/88d9e12ae178fab0fb5cc050a94da85685d449ea/library/compiler-builtins/libm/src/math/cbrt.rs) | `f73e293daa12abe52dd73882816fe4002b4f6e69065892abd57881180fb26bf2` |
| 18 | [compiler-builtins/libm source notice 7](https://raw.githubusercontent.com/rust-lang/rust/88d9e12ae178fab0fb5cc050a94da85685d449ea/library/compiler-builtins/libm/src/math/cbrt.rs) | `225167f203ade8fe5b0714aed4abf358d23fb9dd385a63580ba216ac88a7b799` |
| 19 | [compiler-builtins/libm source notice 8](https://raw.githubusercontent.com/rust-lang/rust/88d9e12ae178fab0fb5cc050a94da85685d449ea/library/compiler-builtins/libm/src/math/cos.rs) | `f53a3c97c9531345379adbe79db501062242feefd51de57162178585ea62788f` |
| 20 | [compiler-builtins/libm source notice 9](https://raw.githubusercontent.com/rust-lang/rust/88d9e12ae178fab0fb5cc050a94da85685d449ea/library/compiler-builtins/libm/src/math/exp.rs) | `15e78071c8347a689c48ec92bbdd5eba7bd8dc74f86ee3e7ad31d34b24fefe83` |
| 21 | [compiler-builtins/libm source notice 10](https://raw.githubusercontent.com/rust-lang/rust/88d9e12ae178fab0fb5cc050a94da85685d449ea/library/compiler-builtins/libm/src/math/exp2.rs) | `b24dea27b63aa91f44ce04d53d6e84d8b7a76faef78a2224ed2a751a6aa6c0bd` |
| 22 | [compiler-builtins/libm source notice 11](https://raw.githubusercontent.com/rust-lang/rust/88d9e12ae178fab0fb5cc050a94da85685d449ea/library/compiler-builtins/libm/src/math/exp2f.rs) | `9b9ce313edd1fd1cdd9e16ec7ffe26e9b4ea644d4da334618edef2ef79d180fd` |
| 23 | [compiler-builtins/libm source notice 12](https://raw.githubusercontent.com/rust-lang/rust/88d9e12ae178fab0fb5cc050a94da85685d449ea/library/compiler-builtins/libm/src/math/fmaf16.rs) | `80ef000233f022bf9edb37d7237c8266453c31bfa014693626cdbb281d6e5504` |
| 24 | [compiler-builtins/libm source notice 13](https://raw.githubusercontent.com/rust-lang/rust/88d9e12ae178fab0fb5cc050a94da85685d449ea/library/compiler-builtins/libm/src/math/generic/floor.rs) | `cc7ddd4aec15d176fc40570979991b6bbe00d3ec3d9bbf7c5dd8bfe0e04278b9` |
| 25 | [compiler-builtins/libm source notice 14](https://raw.githubusercontent.com/rust-lang/rust/88d9e12ae178fab0fb5cc050a94da85685d449ea/library/compiler-builtins/libm/src/math/generic/fmax.rs) | `a997449a08324894b63f1159f979a44ef52a95bd4a0d2374c76e2e1b87e50963` |
| 26 | [compiler-builtins/libm source notice 15](https://raw.githubusercontent.com/rust-lang/rust/88d9e12ae178fab0fb5cc050a94da85685d449ea/library/compiler-builtins/libm/src/math/generic/trunc.rs) | `b1e0b197ab096657b36a818578674de4e4af0030827bf8a6e5b4405fafda000d` |
| 27 | [compiler-builtins/libm source notice 16](https://raw.githubusercontent.com/rust-lang/rust/88d9e12ae178fab0fb5cc050a94da85685d449ea/library/compiler-builtins/libm/src/math/hypot.rs) | `17050c001d36064dbfc01fdbd90b93b7ea0ab69524394915e97cf654ede6e4bd` |
| 28 | [compiler-builtins/libm source notice 17](https://raw.githubusercontent.com/rust-lang/rust/88d9e12ae178fab0fb5cc050a94da85685d449ea/library/compiler-builtins/libm/src/math/k_cos.rs) | `50dc54cee8b36780c535e15a6d30d75226fe2bdb81d450e375118e42bd3bc659` |
| 29 | [compiler-builtins/libm source notice 18](https://raw.githubusercontent.com/rust-lang/rust/88d9e12ae178fab0fb5cc050a94da85685d449ea/library/compiler-builtins/libm/src/math/k_sin.rs) | `a04bfa8426560b34a6dc330fb3f55ebc8def1092f0cf2faf86093bcd5d7c82aa` |
| 30 | [compiler-builtins/libm source notice 19](https://raw.githubusercontent.com/rust-lang/rust/88d9e12ae178fab0fb5cc050a94da85685d449ea/library/compiler-builtins/libm/src/math/k_tan.rs) | `68726120d836ef745923a5412ad99f724cec7a97e053c0b1c1dd6639336c167e` |
| 31 | [compiler-builtins/libm source notice 20](https://raw.githubusercontent.com/rust-lang/rust/88d9e12ae178fab0fb5cc050a94da85685d449ea/library/compiler-builtins/libm/src/math/k_tanf.rs) | `5a534ed1f9266ed799e9f2e3f830f15411762021829da121b23df5edc498c6b6` |
| 32 | [compiler-builtins/libm source notice 21](https://raw.githubusercontent.com/rust-lang/rust/88d9e12ae178fab0fb5cc050a94da85685d449ea/library/compiler-builtins/libm/src/math/rem_pio2.rs) | `370d3d0ecd600731cb6b225e4a037d18d9ab48fac6a463563f088227c2fd0964` |
| 33 | [compiler-builtins/libm source notice 22](https://raw.githubusercontent.com/rust-lang/rust/88d9e12ae178fab0fb5cc050a94da85685d449ea/library/compiler-builtins/libm/src/math/sin.rs) | `aa31720e0a22f01d45849f4f5badc7a75fbabc8073b08df329baa9305cf2c682` |
| 34 | [compiler-builtins/libm source notice 23](https://raw.githubusercontent.com/rust-lang/rust/88d9e12ae178fab0fb5cc050a94da85685d449ea/library/compiler-builtins/libm/src/math/tan.rs) | `3c85590cea896eaab7cb8a8f4474902c6b44fb0d67ee569737cac1942f9933a9` |
| 35 | [addr2line 0.25.1 / LICENSE-APACHE](https://docs.rs/crate/addr2line/0.25.1/source/LICENSE-APACHE) | `a60eea817514531668d7e00765731449fe14d059d3249e0bc93b36de45f759f2` |
| 36 | [addr2line 0.25.1 / LICENSE-MIT](https://docs.rs/crate/addr2line/0.25.1/source/LICENSE-MIT) | `e99d88d232bf57d70f0fb87f6b496d44b6653f99f8a63d250a54c61ea4bcde40` |
| 37 | [adler2 2.0.1 / LICENSE-0BSD](https://docs.rs/crate/adler2/2.0.1/source/LICENSE-0BSD) | `861399f8c21c042b110517e76dc6b63a2b334276c8cf17412fc3c8908ca8dc17` |
| 38 | [adler2 2.0.1 / LICENSE-APACHE](https://docs.rs/crate/adler2/2.0.1/source/LICENSE-APACHE) | `8ada45cd9f843acf64e4722ae262c622a2b3b3007c7310ef36ac1061a30f6adb` |
| 39 | [adler2 2.0.1 / LICENSE-MIT](https://docs.rs/crate/adler2/2.0.1/source/LICENSE-MIT) | `23f18e03dc49df91622fe2a76176497404e46ced8a715d9d2b67a7446571cca3` |
| 40 | [cc 1.2.0 / LICENSE-APACHE](https://docs.rs/crate/cc/1.2.0/source/LICENSE-APACHE) | `a60eea817514531668d7e00765731449fe14d059d3249e0bc93b36de45f759f2` |
| 41 | [cc 1.2.0 / LICENSE-MIT](https://docs.rs/crate/cc/1.2.0/source/LICENSE-MIT) | `378f5840b258e2779c39418f3f2d7b2ba96f1c7917dd6be0713f88305dbda397` |
| 42 | [cfg-if 1.0.4 / LICENSE-APACHE](https://docs.rs/crate/cfg-if/1.0.4/source/LICENSE-APACHE) | `a60eea817514531668d7e00765731449fe14d059d3249e0bc93b36de45f759f2` |
| 43 | [cfg-if 1.0.4 / LICENSE-MIT](https://docs.rs/crate/cfg-if/1.0.4/source/LICENSE-MIT) | `378f5840b258e2779c39418f3f2d7b2ba96f1c7917dd6be0713f88305dbda397` |
| 44 | [dlmalloc 0.2.13 / LICENSE-APACHE](https://docs.rs/crate/dlmalloc/0.2.13/source/LICENSE-APACHE) | `a60eea817514531668d7e00765731449fe14d059d3249e0bc93b36de45f759f2` |
| 45 | [dlmalloc 0.2.13 / LICENSE-MIT](https://docs.rs/crate/dlmalloc/0.2.13/source/LICENSE-MIT) | `378f5840b258e2779c39418f3f2d7b2ba96f1c7917dd6be0713f88305dbda397` |
| 46 | [foldhash 0.2.0 / LICENSE](https://docs.rs/crate/foldhash/0.2.0/source/LICENSE) | `b1181a40b2a7b25cf66fd01481713bc1005df082c53ef73e851e55071b102744` |
| 47 | [getopts 0.2.24 / LICENSE-APACHE](https://docs.rs/crate/getopts/0.2.24/source/LICENSE-APACHE) | `a60eea817514531668d7e00765731449fe14d059d3249e0bc93b36de45f759f2` |
| 48 | [getopts 0.2.24 / LICENSE-MIT](https://docs.rs/crate/getopts/0.2.24/source/LICENSE-MIT) | `6485b8ed310d3f0340bf1ad1f47645069ce4069dcc6bb46c7d5c6faf41de1fdb` |
| 49 | [gimli 0.32.3 / LICENSE-APACHE](https://docs.rs/crate/gimli/0.32.3/source/LICENSE-APACHE) | `a60eea817514531668d7e00765731449fe14d059d3249e0bc93b36de45f759f2` |
| 50 | [gimli 0.32.3 / LICENSE-MIT](https://docs.rs/crate/gimli/0.32.3/source/LICENSE-MIT) | `7b63ecd5f1902af1b63729947373683c32745c16a10e8e6292e2e2dcd7e90ae0` |
| 51 | [hashbrown 0.17.1 / LICENSE-APACHE](https://docs.rs/crate/hashbrown/0.17.1/source/LICENSE-APACHE) | `a60eea817514531668d7e00765731449fe14d059d3249e0bc93b36de45f759f2` |
| 52 | [hashbrown 0.17.1 / LICENSE-MIT](https://docs.rs/crate/hashbrown/0.17.1/source/LICENSE-MIT) | `ff8f68cb076caf8cefe7a6430d4ac086ce6af2ca8ce2c4e5a2004d4552ef52a2` |
| 53 | [hermit-abi 0.5.2 / LICENSE-APACHE](https://docs.rs/crate/hermit-abi/0.5.2/source/LICENSE-APACHE) | `a60eea817514531668d7e00765731449fe14d059d3249e0bc93b36de45f759f2` |
| 54 | [hermit-abi 0.5.2 / LICENSE-MIT](https://docs.rs/crate/hermit-abi/0.5.2/source/LICENSE-MIT) | `23f18e03dc49df91622fe2a76176497404e46ced8a715d9d2b67a7446571cca3` |
| 55 | [libc 0.2.185 / LICENSE-APACHE](https://docs.rs/crate/libc/0.2.185/source/LICENSE-APACHE) | `62c7a1e35f56406896d7aa7ca52d0cc0d272ac022b5d2796e7d6905db8a3636a` |
| 56 | [libc 0.2.185 / LICENSE-MIT](https://docs.rs/crate/libc/0.2.185/source/LICENSE-MIT) | `123a331b5dbf04c30097fa43b8f858bc85df671fe776de498d01f3d6b7c1f69e` |
| 57 | [memchr 2.7.6 / LICENSE-MIT](https://docs.rs/crate/memchr/2.7.6/source/LICENSE-MIT) | `0f96a83840e146e43c0ec96a22ec1f392e0680e6c1226e6f3ba87e0740af850f` |
| 58 | [memchr 2.7.6 / UNLICENSE](https://docs.rs/crate/memchr/2.7.6/source/UNLICENSE) | `7e12e5df4bae12cb21581ba157ced20e1986a0508dd10d0e8a4ab9a4cf94e85c` |
| 59 | [miniz_oxide 0.8.9 / LICENSE](https://docs.rs/crate/miniz_oxide/0.8.9/source/LICENSE) | `4108245a1f2df9d4e94df8abed5b4ba0759bb2f9b40a6b939f1be141077ae50b` |
| 60 | [miniz_oxide 0.8.9 / LICENSE-APACHE.md](https://docs.rs/crate/miniz_oxide/0.8.9/source/LICENSE-APACHE.md) | `0d542e0c8804e39aa7f37eb00da5a762149dc682d7829451287e11b938e94594` |
| 61 | [miniz_oxide 0.8.9 / LICENSE-MIT.md](https://docs.rs/crate/miniz_oxide/0.8.9/source/LICENSE-MIT.md) | `799e9ca9d179295ef372f25d3769cdda7d25bb2668add6a6a1e22d1e4c678b8d` |
| 62 | [miniz_oxide 0.8.9 / LICENSE-ZLIB.md](https://docs.rs/crate/miniz_oxide/0.8.9/source/LICENSE-ZLIB.md) | `0a54e647fe54104658b5e563c04c6f9edf251710e47bce692e0bd990a4ddaa39` |
| 63 | [moto-rt 0.16.0 / LICENSE-APACHE](https://docs.rs/crate/moto-rt/0.16.0/source/LICENSE-APACHE) | `69fef7b0f322a65554156141f2bc6256ed0bb78cba7e49f0d8829d7ec4ee62cd` |
| 64 | [moto-rt 0.16.0 / LICENSE-MIT](https://docs.rs/crate/moto-rt/0.16.0/source/LICENSE-MIT) | `a7c936ff1ed8fa340172d42a98185afee078f818e907da69f3a8e336b1623b4b` |
| 65 | [object 0.37.3 / LICENSE-APACHE](https://docs.rs/crate/object/0.37.3/source/LICENSE-APACHE) | `a60eea817514531668d7e00765731449fe14d059d3249e0bc93b36de45f759f2` |
| 66 | [object 0.37.3 / LICENSE-MIT](https://docs.rs/crate/object/0.37.3/source/LICENSE-MIT) | `0b74dfa0bcee5c420c6b7f67b4b2658f9ab8388c97b8e733975f2cecbdd668a6` |
| 67 | [r-efi 5.3.0 / AUTHORS](https://docs.rs/crate/r-efi/5.3.0/source/AUTHORS) | `ff92bed461f50338dd703a9ba9aee496a425957873df1d91192776e4bdf5dda7` |
| 68 | [r-efi-alloc 2.1.0 / AUTHORS](https://docs.rs/crate/r-efi-alloc/2.1.0/source/AUTHORS) | `fba3318d0c600380177edf7ba4778ff6588faa8d106c679ac5dfee620011239c` |
| 69 | [rand 0.9.2 / COPYRIGHT](https://docs.rs/crate/rand/0.9.2/source/COPYRIGHT) | `90eb64f0279b0d9432accfa6023ff803bc4965212383697eee27a0f426d5f8d5` |
| 70 | [rand 0.9.2 / LICENSE-APACHE](https://docs.rs/crate/rand/0.9.2/source/LICENSE-APACHE) | `35242e7a83f69875e6edeff02291e688c97caafe2f8902e4e19b49d3e78b4cab` |
| 71 | [rand 0.9.2 / LICENSE-MIT](https://docs.rs/crate/rand/0.9.2/source/LICENSE-MIT) | `209fbbe0ad52d9235e37badf9cadfe4dbdc87203179c0899e738b39ade42177b` |
| 72 | [rand_core 0.9.3 / COPYRIGHT](https://docs.rs/crate/rand_core/0.9.3/source/COPYRIGHT) | `90eb64f0279b0d9432accfa6023ff803bc4965212383697eee27a0f426d5f8d5` |
| 73 | [rand_core 0.9.3 / LICENSE-APACHE](https://docs.rs/crate/rand_core/0.9.3/source/LICENSE-APACHE) | `6df43f6f4b5d4587f3d8d71e45532c688fd168afa5fe89d571cb32fa09c4ef51` |
| 74 | [rand_core 0.9.3 / LICENSE-MIT](https://docs.rs/crate/rand_core/0.9.3/source/LICENSE-MIT) | `209fbbe0ad52d9235e37badf9cadfe4dbdc87203179c0899e738b39ade42177b` |
| 75 | [rand_xorshift 0.4.0 / COPYRIGHT](https://docs.rs/crate/rand_xorshift/0.4.0/source/COPYRIGHT) | `90eb64f0279b0d9432accfa6023ff803bc4965212383697eee27a0f426d5f8d5` |
| 76 | [rand_xorshift 0.4.0 / LICENSE-APACHE](https://docs.rs/crate/rand_xorshift/0.4.0/source/LICENSE-APACHE) | `35242e7a83f69875e6edeff02291e688c97caafe2f8902e4e19b49d3e78b4cab` |
| 77 | [rand_xorshift 0.4.0 / LICENSE-MIT](https://docs.rs/crate/rand_xorshift/0.4.0/source/LICENSE-MIT) | `209fbbe0ad52d9235e37badf9cadfe4dbdc87203179c0899e738b39ade42177b` |
| 78 | [rustc-demangle 0.1.27 / LICENSE-APACHE](https://docs.rs/crate/rustc-demangle/0.1.27/source/LICENSE-APACHE) | `a60eea817514531668d7e00765731449fe14d059d3249e0bc93b36de45f759f2` |
| 79 | [rustc-demangle 0.1.27 / LICENSE-MIT](https://docs.rs/crate/rustc-demangle/0.1.27/source/LICENSE-MIT) | `378f5840b258e2779c39418f3f2d7b2ba96f1c7917dd6be0713f88305dbda397` |
| 80 | [rustc-literal-escaper 0.0.8 / LICENSE-APACHE](https://docs.rs/crate/rustc-literal-escaper/0.0.8/source/LICENSE-APACHE) | `62c7a1e35f56406896d7aa7ca52d0cc0d272ac022b5d2796e7d6905db8a3636a` |
| 81 | [rustc-literal-escaper 0.0.8 / LICENSE-MIT](https://docs.rs/crate/rustc-literal-escaper/0.0.8/source/LICENSE-MIT) | `23f18e03dc49df91622fe2a76176497404e46ced8a715d9d2b67a7446571cca3` |
| 82 | [shlex 1.3.0 / LICENSE-APACHE](https://docs.rs/crate/shlex/1.3.0/source/LICENSE-APACHE) | `553fffcd9b1cb158bc3e9edc35da85ca5c3b3d7d2e61c883ebcfa8a65814b583` |
| 83 | [shlex 1.3.0 / LICENSE-MIT](https://docs.rs/crate/shlex/1.3.0/source/LICENSE-MIT) | `4455bf75a91154108304cb283e0fea9948c14f13e20d60887cf2552449dea3b1` |
| 84 | [unwinding 0.2.8 / LICENSE-APACHE](https://docs.rs/crate/unwinding/0.2.8/source/LICENSE-APACHE) | `62c7a1e35f56406896d7aa7ca52d0cc0d272ac022b5d2796e7d6905db8a3636a` |
| 85 | [unwinding 0.2.8 / LICENSE-MIT](https://docs.rs/crate/unwinding/0.2.8/source/LICENSE-MIT) | `23f18e03dc49df91622fe2a76176497404e46ced8a715d9d2b67a7446571cca3` |
| 86 | [wasip2 1.0.3+wasi-0.2.9 / LICENSE-APACHE](https://docs.rs/crate/wasip2/1.0.3+wasi-0.2.9/source/LICENSE-APACHE) | `a60eea817514531668d7e00765731449fe14d059d3249e0bc93b36de45f759f2` |
| 87 | [wasip2 1.0.3+wasi-0.2.9 / LICENSE-Apache-2.0_WITH_LLVM-exception](https://docs.rs/crate/wasip2/1.0.3+wasi-0.2.9/source/LICENSE-Apache-2.0_WITH_LLVM-exception) | `268872b9816f90fd8e85db5a28d33f8150ebb8dd016653fb39ef1f94f2686bc5` |
| 88 | [wasip2 1.0.3+wasi-0.2.9 / LICENSE-MIT](https://docs.rs/crate/wasip2/1.0.3+wasi-0.2.9/source/LICENSE-MIT) | `23f18e03dc49df91622fe2a76176497404e46ced8a715d9d2b67a7446571cca3` |
| 89 | [wasip3 0.6.0+wasi-0.3.0-rc-2026-03-15 / LICENSE-APACHE](https://docs.rs/crate/wasip3/0.6.0+wasi-0.3.0-rc-2026-03-15/source/LICENSE-APACHE) | `a60eea817514531668d7e00765731449fe14d059d3249e0bc93b36de45f759f2` |
| 90 | [wasip3 0.6.0+wasi-0.3.0-rc-2026-03-15 / LICENSE-Apache-2.0_WITH_LLVM-exception](https://docs.rs/crate/wasip3/0.6.0+wasi-0.3.0-rc-2026-03-15/source/LICENSE-Apache-2.0_WITH_LLVM-exception) | `268872b9816f90fd8e85db5a28d33f8150ebb8dd016653fb39ef1f94f2686bc5` |
| 91 | [wasip3 0.6.0+wasi-0.3.0-rc-2026-03-15 / LICENSE-MIT](https://docs.rs/crate/wasip3/0.6.0+wasi-0.3.0-rc-2026-03-15/source/LICENSE-MIT) | `23f18e03dc49df91622fe2a76176497404e46ced8a715d9d2b67a7446571cca3` |
| 92 | [wit-bindgen 0.57.1 / LICENSE-APACHE](https://docs.rs/crate/wit-bindgen/0.57.1/source/LICENSE-APACHE) | `a60eea817514531668d7e00765731449fe14d059d3249e0bc93b36de45f759f2` |
| 93 | [wit-bindgen 0.57.1 / LICENSE-Apache-2.0_WITH_LLVM-exception](https://docs.rs/crate/wit-bindgen/0.57.1/source/LICENSE-Apache-2.0_WITH_LLVM-exception) | `268872b9816f90fd8e85db5a28d33f8150ebb8dd016653fb39ef1f94f2686bc5` |
| 94 | [wit-bindgen 0.57.1 / LICENSE-MIT](https://docs.rs/crate/wit-bindgen/0.57.1/source/LICENSE-MIT) | `23f18e03dc49df91622fe2a76176497404e46ced8a715d9d2b67a7446571cca3` |
| 95 | [fortanix-sgx-abi/LICENSE](https://raw.githubusercontent.com/fortanix/rust-sgx/adcc6cdb981bcf2101437edbd3ef6144246621d3/LICENSE) | `fab3dd6bdab226f1c08630b1dd917e11fcb4ec5e1e020e2c16f83a0a13863e85` |
| 96 | [vex-sdk/LICENSE.md](https://raw.githubusercontent.com/vexide/vex-sdk/6c0b83d35b6fc31a7607efe456e387df45476314/LICENSE.md) | `7bc7d5de2a0b793d4e1adb7ffcb99387776adc1f15f055bc3b667ea449249e6a` |
| 97 | [wasip1/LICENSE-APACHE](https://raw.githubusercontent.com/bytecodealliance/wasi-rs/e55b3b02fcda2a016f5df14573ff05c5b1e313d6/LICENSE-APACHE) | `a60eea817514531668d7e00765731449fe14d059d3249e0bc93b36de45f759f2` |
| 98 | [wasip1/LICENSE-Apache-2.0_WITH_LLVM-exception](https://raw.githubusercontent.com/bytecodealliance/wasi-rs/e55b3b02fcda2a016f5df14573ff05c5b1e313d6/LICENSE-Apache-2.0_WITH_LLVM-exception) | `268872b9816f90fd8e85db5a28d33f8150ebb8dd016653fb39ef1f94f2686bc5` |
| 99 | [wasip1/LICENSE-MIT](https://raw.githubusercontent.com/bytecodealliance/wasi-rs/e55b3b02fcda2a016f5df14573ff05c5b1e313d6/LICENSE-MIT) | `23f18e03dc49df91622fe2a76176497404e46ced8a715d9d2b67a7446571cca3` |

## Runtime source comment map

Paths below are relative to Rust's `library/compiler-builtins` directory at
the pinned commit. The notice file maps every listed file to its numbered
comment section. These hashes identify whole source files, not copied code
in the dashboard.

| File | Source-file SHA-256 | Notice sections |
| --- | --- | --- |
| [compiler-builtins/src/probestack.rs](https://raw.githubusercontent.com/rust-lang/rust/88d9e12ae178fab0fb5cc050a94da85685d449ea/library/compiler-builtins/compiler-builtins/src/probestack.rs) | `0ee11ae0cce89f3727341bb825e235736a4f666c5f04c9ac5303ba2f30fa460b` | 12 |
| [libm/src/math/acos.rs](https://raw.githubusercontent.com/rust-lang/rust/88d9e12ae178fab0fb5cc050a94da85685d449ea/library/compiler-builtins/libm/src/math/acos.rs) | `efd83a6138061209867f06421c6a756b8ca3aab5c6c0a85c4aba31bedb299da5` | 13 |
| [libm/src/math/acosf.rs](https://raw.githubusercontent.com/rust-lang/rust/88d9e12ae178fab0fb5cc050a94da85685d449ea/library/compiler-builtins/libm/src/math/acosf.rs) | `c5c9c74d267725bbe5f9319e6f4a396af6ce46c890b601d3f963a5d74d12e4c3` | 14 |
| [libm/src/math/approx/cbrtf64.rs](https://raw.githubusercontent.com/rust-lang/rust/88d9e12ae178fab0fb5cc050a94da85685d449ea/library/compiler-builtins/libm/src/math/approx/cbrtf64.rs) | `7344cf87169de41ffcf2f94d356603cf5093353e7e9b52bfbfb3c34206d5264e` | 15 |
| [libm/src/math/asin.rs](https://raw.githubusercontent.com/rust-lang/rust/88d9e12ae178fab0fb5cc050a94da85685d449ea/library/compiler-builtins/libm/src/math/asin.rs) | `9693a7cb61674ec6fe22be9db8fe20276729c48b1562fd86d5421e372a0a12e4` | 13 |
| [libm/src/math/asinf.rs](https://raw.githubusercontent.com/rust-lang/rust/88d9e12ae178fab0fb5cc050a94da85685d449ea/library/compiler-builtins/libm/src/math/asinf.rs) | `25f6b7bbf46fc5aac4ebdd9e00118239747dcd28a79b90b858ddc4633d773d65` | 14 |
| [libm/src/math/atan.rs](https://raw.githubusercontent.com/rust-lang/rust/88d9e12ae178fab0fb5cc050a94da85685d449ea/library/compiler-builtins/libm/src/math/atan.rs) | `54181dbddf4f77b90b32b9f6819ac989da570dc02b791f3685d20a23d51324be` | 14 |
| [libm/src/math/atan2.rs](https://raw.githubusercontent.com/rust-lang/rust/88d9e12ae178fab0fb5cc050a94da85685d449ea/library/compiler-builtins/libm/src/math/atan2.rs) | `bb042e1e1b9f080bba1c7b29947f6c4ec9413807fae95000df2c4b50ab97650b` | 16 |
| [libm/src/math/atan2f.rs](https://raw.githubusercontent.com/rust-lang/rust/88d9e12ae178fab0fb5cc050a94da85685d449ea/library/compiler-builtins/libm/src/math/atan2f.rs) | `b9497b5adb75e2788c404fd121aa64dcfa0e9357b67af3b3e7e6d4624dae1ca1` | 14 |
| [libm/src/math/atanf.rs](https://raw.githubusercontent.com/rust-lang/rust/88d9e12ae178fab0fb5cc050a94da85685d449ea/library/compiler-builtins/libm/src/math/atanf.rs) | `197762f16ed5951236e17d0869c529e857acc329cc287df019ff69e5401f3b4d` | 14 |
| [libm/src/math/cbrt.rs](https://raw.githubusercontent.com/rust-lang/rust/88d9e12ae178fab0fb5cc050a94da85685d449ea/library/compiler-builtins/libm/src/math/cbrt.rs) | `6f25fdb3b8c5cb5950f8d82e28c36a099865bbd4048cf7924e70b72b6f997822` | 17, 18 |
| [libm/src/math/cbrtf.rs](https://raw.githubusercontent.com/rust-lang/rust/88d9e12ae178fab0fb5cc050a94da85685d449ea/library/compiler-builtins/libm/src/math/cbrtf.rs) | `7a2b4f190e8618c52cb23b47c040a4b4eacca739c68393cf14fb3f5d73988127` | 14 |
| [libm/src/math/cos.rs](https://raw.githubusercontent.com/rust-lang/rust/88d9e12ae178fab0fb5cc050a94da85685d449ea/library/compiler-builtins/libm/src/math/cos.rs) | `6e81752899c471bd42d04bdf08e0b22bc10a29f92fa2a343281b995fd342f035` | 19 |
| [libm/src/math/cosf.rs](https://raw.githubusercontent.com/rust-lang/rust/88d9e12ae178fab0fb5cc050a94da85685d449ea/library/compiler-builtins/libm/src/math/cosf.rs) | `f48aa954f199fe4f54fd73b4e7886a75f2c08afba7fc31c6a36db5b5f90ced69` | 14 |
| [libm/src/math/erf.rs](https://raw.githubusercontent.com/rust-lang/rust/88d9e12ae178fab0fb5cc050a94da85685d449ea/library/compiler-builtins/libm/src/math/erf.rs) | `5a69f440d5ca88a5c4a45119837c45b14238836a036675230e10fec41d293a3b` | 14 |
| [libm/src/math/erff.rs](https://raw.githubusercontent.com/rust-lang/rust/88d9e12ae178fab0fb5cc050a94da85685d449ea/library/compiler-builtins/libm/src/math/erff.rs) | `62c30876390e532d7dc9ac1f1f1c1f654065fba2b2e534e531964d79e1766196` | 14 |
| [libm/src/math/exp.rs](https://raw.githubusercontent.com/rust-lang/rust/88d9e12ae178fab0fb5cc050a94da85685d449ea/library/compiler-builtins/libm/src/math/exp.rs) | `f48970e2b92d4cc0e997abcd9d54bfaf6b8dd689a8b62916ff1fcc396f6ef700` | 20 |
| [libm/src/math/exp2.rs](https://raw.githubusercontent.com/rust-lang/rust/88d9e12ae178fab0fb5cc050a94da85685d449ea/library/compiler-builtins/libm/src/math/exp2.rs) | `7e73d7528d1407670d123a17a207242e98eb2f829a9b0dc91071200379adf63c` | 21 |
| [libm/src/math/exp2f.rs](https://raw.githubusercontent.com/rust-lang/rust/88d9e12ae178fab0fb5cc050a94da85685d449ea/library/compiler-builtins/libm/src/math/exp2f.rs) | `1c0f02200a85a01e37b4657076963e0c6670f8f3d9dd1db80a64cec268d40027` | 22 |
| [libm/src/math/expf.rs](https://raw.githubusercontent.com/rust-lang/rust/88d9e12ae178fab0fb5cc050a94da85685d449ea/library/compiler-builtins/libm/src/math/expf.rs) | `983bf3922bed804ec889890a0e7d31641fd7098588645dad47920915c2753950` | 14 |
| [libm/src/math/expm1.rs](https://raw.githubusercontent.com/rust-lang/rust/88d9e12ae178fab0fb5cc050a94da85685d449ea/library/compiler-builtins/libm/src/math/expm1.rs) | `6cc612ca7cf1384dc15f541b301a1f176efbc79536aff1293408b3121cc30ad3` | 14 |
| [libm/src/math/expm1f.rs](https://raw.githubusercontent.com/rust-lang/rust/88d9e12ae178fab0fb5cc050a94da85685d449ea/library/compiler-builtins/libm/src/math/expm1f.rs) | `2b517f1bcb6279c906aad737f08c83ac30d7871e3beff266ea50a01fa18ccc81` | 14 |
| [libm/src/math/fma.rs](https://raw.githubusercontent.com/rust-lang/rust/88d9e12ae178fab0fb5cc050a94da85685d449ea/library/compiler-builtins/libm/src/math/fma.rs) | `680cf8fa506f9438b6b97afd1868c9c5860629047f79acf4ce474045dd7af06b` | 17 |
| [libm/src/math/fmaf16.rs](https://raw.githubusercontent.com/rust-lang/rust/88d9e12ae178fab0fb5cc050a94da85685d449ea/library/compiler-builtins/libm/src/math/fmaf16.rs) | `b2f1e0cf98f87bcf4b08e8071aa29dc715591a7099d0e25ccd2a99f96d0167c2` | 23 |
| [libm/src/math/generic/ceil.rs](https://raw.githubusercontent.com/rust-lang/rust/88d9e12ae178fab0fb5cc050a94da85685d449ea/library/compiler-builtins/libm/src/math/generic/ceil.rs) | `bf033f078eee7ad8b2d4c4ebe16316a050c00ca1344ef58501a91dc84a62f92e` | 17 |
| [libm/src/math/generic/floor.rs](https://raw.githubusercontent.com/rust-lang/rust/88d9e12ae178fab0fb5cc050a94da85685d449ea/library/compiler-builtins/libm/src/math/generic/floor.rs) | `d736b5f56a5623a9c8f08eb3adeef6ff022977fa3de693b74cb249adf1ce8c62` | 24 |
| [libm/src/math/generic/fma.rs](https://raw.githubusercontent.com/rust-lang/rust/88d9e12ae178fab0fb5cc050a94da85685d449ea/library/compiler-builtins/libm/src/math/generic/fma.rs) | `ba44930a56d7c1953ab0c96c7c767911c9dc6967083700c8ef02f43672c8482f` | 17 |
| [libm/src/math/generic/fmax.rs](https://raw.githubusercontent.com/rust-lang/rust/88d9e12ae178fab0fb5cc050a94da85685d449ea/library/compiler-builtins/libm/src/math/generic/fmax.rs) | `36bf5260cfcff74e2e59972fcadd066496036ef888b60c94510be503832c52b4` | 25 |
| [libm/src/math/generic/fmaximum.rs](https://raw.githubusercontent.com/rust-lang/rust/88d9e12ae178fab0fb5cc050a94da85685d449ea/library/compiler-builtins/libm/src/math/generic/fmaximum.rs) | `d085799dc1a625d96158ed9595447c157fe75cd183da75edccb02f2cb6c7224c` | 25 |
| [libm/src/math/generic/fmaximum_num.rs](https://raw.githubusercontent.com/rust-lang/rust/88d9e12ae178fab0fb5cc050a94da85685d449ea/library/compiler-builtins/libm/src/math/generic/fmaximum_num.rs) | `ad9a820c227d7e7d493cccf14856ba9928a7dc5f973660ee9865938aeff6b086` | 25 |
| [libm/src/math/generic/fmin.rs](https://raw.githubusercontent.com/rust-lang/rust/88d9e12ae178fab0fb5cc050a94da85685d449ea/library/compiler-builtins/libm/src/math/generic/fmin.rs) | `92dc4c0889bcde4c42e99329e179c945f6e824b2775a5162152fb1b58dd823a5` | 25 |
| [libm/src/math/generic/fminimum.rs](https://raw.githubusercontent.com/rust-lang/rust/88d9e12ae178fab0fb5cc050a94da85685d449ea/library/compiler-builtins/libm/src/math/generic/fminimum.rs) | `3071d662c6c4c9c8129c46b63f32b979430e823359ee4c95cf3619e81fbc5be6` | 25 |
| [libm/src/math/generic/fminimum_num.rs](https://raw.githubusercontent.com/rust-lang/rust/88d9e12ae178fab0fb5cc050a94da85685d449ea/library/compiler-builtins/libm/src/math/generic/fminimum_num.rs) | `81aabcbadce6b93d15ae02d6eecd50aeec8c7bd24dc919881e6ca3648a6da7ca` | 25 |
| [libm/src/math/generic/fmod.rs](https://raw.githubusercontent.com/rust-lang/rust/88d9e12ae178fab0fb5cc050a94da85685d449ea/library/compiler-builtins/libm/src/math/generic/fmod.rs) | `11aae62d08d656d815184eafd78a3b2d5764b6ce308b89e3819cd049bc491fe7` | 25 |
| [libm/src/math/generic/rint.rs](https://raw.githubusercontent.com/rust-lang/rust/88d9e12ae178fab0fb5cc050a94da85685d449ea/library/compiler-builtins/libm/src/math/generic/rint.rs) | `7340d5558ca3237701fefac52d0c384339a6a3b1ab022d5c3d78220bc8d4ee78` | 17 |
| [libm/src/math/generic/sqrt.rs](https://raw.githubusercontent.com/rust-lang/rust/88d9e12ae178fab0fb5cc050a94da85685d449ea/library/compiler-builtins/libm/src/math/generic/sqrt.rs) | `0cc77367ada05e7b8622c991af0b989ed97c7630ac7a2e9fd192b969203a2705` | 17 |
| [libm/src/math/generic/trunc.rs](https://raw.githubusercontent.com/rust-lang/rust/88d9e12ae178fab0fb5cc050a94da85685d449ea/library/compiler-builtins/libm/src/math/generic/trunc.rs) | `3a3ca091cd1d750276601dc2dfeb2a8ff058636ea2b66a748c55a1804bc8f00a` | 26 |
| [libm/src/math/hypot.rs](https://raw.githubusercontent.com/rust-lang/rust/88d9e12ae178fab0fb5cc050a94da85685d449ea/library/compiler-builtins/libm/src/math/hypot.rs) | `9fe0c6983bd0ab90984b40ff7905d17caee6423a772264846e6ae54e2e8f4403` | 17, 27 |
| [libm/src/math/j0.rs](https://raw.githubusercontent.com/rust-lang/rust/88d9e12ae178fab0fb5cc050a94da85685d449ea/library/compiler-builtins/libm/src/math/j0.rs) | `1cb991f0151a626744ad1832d4aedfaca681bcfcecc797de5ac5e9baf2342006` | 13 |
| [libm/src/math/j0f.rs](https://raw.githubusercontent.com/rust-lang/rust/88d9e12ae178fab0fb5cc050a94da85685d449ea/library/compiler-builtins/libm/src/math/j0f.rs) | `de97374b6a0019fd8b3d1f553be1708018546491d3b4a5fc650ee2dcdfc9620e` | 14 |
| [libm/src/math/j1.rs](https://raw.githubusercontent.com/rust-lang/rust/88d9e12ae178fab0fb5cc050a94da85685d449ea/library/compiler-builtins/libm/src/math/j1.rs) | `e0a10746fd11a458d55ee61e758d9f016ff0f2203a07eb9907b5aca709a40469` | 13 |
| [libm/src/math/j1f.rs](https://raw.githubusercontent.com/rust-lang/rust/88d9e12ae178fab0fb5cc050a94da85685d449ea/library/compiler-builtins/libm/src/math/j1f.rs) | `b747ee287cdddd6d0d0d3f65a186e6605fe6b79bc9c55ffbd27a9205b7a3d07c` | 14 |
| [libm/src/math/jn.rs](https://raw.githubusercontent.com/rust-lang/rust/88d9e12ae178fab0fb5cc050a94da85685d449ea/library/compiler-builtins/libm/src/math/jn.rs) | `ba36d25d4bbada83aadb3b2986879b7d88b91adb7b502061d21bf96ff3942194` | 13 |
| [libm/src/math/jnf.rs](https://raw.githubusercontent.com/rust-lang/rust/88d9e12ae178fab0fb5cc050a94da85685d449ea/library/compiler-builtins/libm/src/math/jnf.rs) | `b3861e19644ae669fe17db81d91aeaee9c9f9051b59957ab674017684c98fe32` | 14 |
| [libm/src/math/k_cos.rs](https://raw.githubusercontent.com/rust-lang/rust/88d9e12ae178fab0fb5cc050a94da85685d449ea/library/compiler-builtins/libm/src/math/k_cos.rs) | `d4867527877c846aa8e2a0b43cf06f03e2d94c321a548acaf8d5171c09ff4057` | 28 |
| [libm/src/math/k_cosf.rs](https://raw.githubusercontent.com/rust-lang/rust/88d9e12ae178fab0fb5cc050a94da85685d449ea/library/compiler-builtins/libm/src/math/k_cosf.rs) | `8f76089d014e217b96c9eca520271e64d467dc0d6542bf313dd70eb96c040f7f` | 14 |
| [libm/src/math/k_sin.rs](https://raw.githubusercontent.com/rust-lang/rust/88d9e12ae178fab0fb5cc050a94da85685d449ea/library/compiler-builtins/libm/src/math/k_sin.rs) | `dfe6524cb2d530ec51eb1a2ab233aeb65b3f12177c1fb2f50f044ea41170f5d8` | 29 |
| [libm/src/math/k_sinf.rs](https://raw.githubusercontent.com/rust-lang/rust/88d9e12ae178fab0fb5cc050a94da85685d449ea/library/compiler-builtins/libm/src/math/k_sinf.rs) | `cf0e8bbe8d73a6704b7f1f710f5b9d31f190f1c633e894d4f69c4e3bc359eba3` | 14 |
| [libm/src/math/k_tan.rs](https://raw.githubusercontent.com/rust-lang/rust/88d9e12ae178fab0fb5cc050a94da85685d449ea/library/compiler-builtins/libm/src/math/k_tan.rs) | `dc3f8ece9187bbd65792a7d1068e676092e9c2a0886f28c80579f9bd94d744b7` | 30 |
| [libm/src/math/k_tanf.rs](https://raw.githubusercontent.com/rust-lang/rust/88d9e12ae178fab0fb5cc050a94da85685d449ea/library/compiler-builtins/libm/src/math/k_tanf.rs) | `3381d2d4865bfc98e87aadb96bd8986f7691a389569a58fb1cb8db74fb5d9c20` | 31 |
| [libm/src/math/lgamma_r.rs](https://raw.githubusercontent.com/rust-lang/rust/88d9e12ae178fab0fb5cc050a94da85685d449ea/library/compiler-builtins/libm/src/math/lgamma_r.rs) | `46d8850ccb72445128e5fd1c15235730298cd1a5e0726abd946f6d0d3605a12d` | 16 |
| [libm/src/math/lgammaf_r.rs](https://raw.githubusercontent.com/rust-lang/rust/88d9e12ae178fab0fb5cc050a94da85685d449ea/library/compiler-builtins/libm/src/math/lgammaf_r.rs) | `c77d6d8a3c9e6f306bfdad3930fb7d96d57fca8fa8c1b666fc65f4db18e190f4` | 14 |
| [libm/src/math/log.rs](https://raw.githubusercontent.com/rust-lang/rust/88d9e12ae178fab0fb5cc050a94da85685d449ea/library/compiler-builtins/libm/src/math/log.rs) | `df5f4d1325e89621bfcd9cf7788d0d7e20a6ef7f429110fe35b370b3c9362615` | 13 |
| [libm/src/math/log10.rs](https://raw.githubusercontent.com/rust-lang/rust/88d9e12ae178fab0fb5cc050a94da85685d449ea/library/compiler-builtins/libm/src/math/log10.rs) | `4a96dd2b6a2a505497f2bb7ea956a50491d068dab1b6055a573aec1724a52e40` | 13 |
| [libm/src/math/log10f.rs](https://raw.githubusercontent.com/rust-lang/rust/88d9e12ae178fab0fb5cc050a94da85685d449ea/library/compiler-builtins/libm/src/math/log10f.rs) | `784a576dd4a4071f4f67c670eb9b221cb03014818c4b2d71afbcbd2211431caf` | 14 |
| [libm/src/math/log1p.rs](https://raw.githubusercontent.com/rust-lang/rust/88d9e12ae178fab0fb5cc050a94da85685d449ea/library/compiler-builtins/libm/src/math/log1p.rs) | `bb8ce741e54d8f001d9c12450a14a663bd413014da2ade9ba1f5fbc9ff0c9a88` | 14 |
| [libm/src/math/log1pf.rs](https://raw.githubusercontent.com/rust-lang/rust/88d9e12ae178fab0fb5cc050a94da85685d449ea/library/compiler-builtins/libm/src/math/log1pf.rs) | `8736222a9b9004b061f1339bb819c63c6b54414d4a01ef79d5f5a5f0a70677b5` | 14 |
| [libm/src/math/log2.rs](https://raw.githubusercontent.com/rust-lang/rust/88d9e12ae178fab0fb5cc050a94da85685d449ea/library/compiler-builtins/libm/src/math/log2.rs) | `1ec0cdd94bf4435ff0402d209089b1bdbcb1ba9a4cd5f77807f9c84cf534f980` | 13 |
| [libm/src/math/log2f.rs](https://raw.githubusercontent.com/rust-lang/rust/88d9e12ae178fab0fb5cc050a94da85685d449ea/library/compiler-builtins/libm/src/math/log2f.rs) | `7b3b17ba08df6b5a57dacf6f945bfe1a3c088bee0eb11e201ac5affc7f65925a` | 14 |
| [libm/src/math/logf.rs](https://raw.githubusercontent.com/rust-lang/rust/88d9e12ae178fab0fb5cc050a94da85685d449ea/library/compiler-builtins/libm/src/math/logf.rs) | `c7dfa5e89fcca0411624fdc594f64c62d5dfa3b91f061fcb7ac3aea18414f238` | 14 |
| [libm/src/math/pow.rs](https://raw.githubusercontent.com/rust-lang/rust/88d9e12ae178fab0fb5cc050a94da85685d449ea/library/compiler-builtins/libm/src/math/pow.rs) | `581efb544966f82f440137757bbbdec99d873c3121efde0294493c4a9fc9d259` | 20 |
| [libm/src/math/powf.rs](https://raw.githubusercontent.com/rust-lang/rust/88d9e12ae178fab0fb5cc050a94da85685d449ea/library/compiler-builtins/libm/src/math/powf.rs) | `819971cb8e927d80113aded3cfe90dcd501c031e0eb1c562b58842fddd1751be` | 14 |
| [libm/src/math/rem_pio2.rs](https://raw.githubusercontent.com/rust-lang/rust/88d9e12ae178fab0fb5cc050a94da85685d449ea/library/compiler-builtins/libm/src/math/rem_pio2.rs) | `931b9b421bc9c485893d5ab1b8c19c67c263ad5e1adab507a00abda5c773336d` | 32 |
| [libm/src/math/rem_pio2_large.rs](https://raw.githubusercontent.com/rust-lang/rust/88d9e12ae178fab0fb5cc050a94da85685d449ea/library/compiler-builtins/libm/src/math/rem_pio2_large.rs) | `494513e8a741239b0e783c5dbf3399ad770e9c8646163ee8694372049b94099a` | 13 |
| [libm/src/math/rem_pio2f.rs](https://raw.githubusercontent.com/rust-lang/rust/88d9e12ae178fab0fb5cc050a94da85685d449ea/library/compiler-builtins/libm/src/math/rem_pio2f.rs) | `e0716d71239538ef8cfa59292bb807d72d43a039a63822b18bd6c2f9a4027116` | 14 |
| [libm/src/math/sin.rs](https://raw.githubusercontent.com/rust-lang/rust/88d9e12ae178fab0fb5cc050a94da85685d449ea/library/compiler-builtins/libm/src/math/sin.rs) | `9a3779d0b2a59cbafcfba0cf1c07ef42747f145b1e149c7d4ad3beb2bbda3fa0` | 33 |
| [libm/src/math/sincos.rs](https://raw.githubusercontent.com/rust-lang/rust/88d9e12ae178fab0fb5cc050a94da85685d449ea/library/compiler-builtins/libm/src/math/sincos.rs) | `a9c414e828c2e78b6bce4d5e4237b1baa93e81d78421aed5cedeab43df64067f` | 14 |
| [libm/src/math/sincosf.rs](https://raw.githubusercontent.com/rust-lang/rust/88d9e12ae178fab0fb5cc050a94da85685d449ea/library/compiler-builtins/libm/src/math/sincosf.rs) | `af8b801067bc05f25763c2c6530330540bc5cb2d6014f400ec41c2706d62c537` | 14 |
| [libm/src/math/sinf.rs](https://raw.githubusercontent.com/rust-lang/rust/88d9e12ae178fab0fb5cc050a94da85685d449ea/library/compiler-builtins/libm/src/math/sinf.rs) | `b17db34d5da39a0f336d831abcb700fab69402c915321823870f1845e66c03d2` | 14 |
| [libm/src/math/support/int_traits/narrowing_div.rs](https://raw.githubusercontent.com/rust-lang/rust/88d9e12ae178fab0fb5cc050a94da85685d449ea/library/compiler-builtins/libm/src/math/support/int_traits/narrowing_div.rs) | `5f5378e33f1da56090d0dce2514926ab465dce65b89ad4ee2f7da07a92f8d5de` | 25 |
| [libm/src/math/support/modular.rs](https://raw.githubusercontent.com/rust-lang/rust/88d9e12ae178fab0fb5cc050a94da85685d449ea/library/compiler-builtins/libm/src/math/support/modular.rs) | `cf6a562d6bdf16336f31d41c59e828ad47f85388849d20d6d3b59fc1c3e388a2` | 25 |
| [libm/src/math/tan.rs](https://raw.githubusercontent.com/rust-lang/rust/88d9e12ae178fab0fb5cc050a94da85685d449ea/library/compiler-builtins/libm/src/math/tan.rs) | `2ea87ca18526753db6d149635ed6c895c8fbafd1e0fa7a9c04f7dc44b4eae986` | 34 |
| [libm/src/math/tanf.rs](https://raw.githubusercontent.com/rust-lang/rust/88d9e12ae178fab0fb5cc050a94da85685d449ea/library/compiler-builtins/libm/src/math/tanf.rs) | `f7140c4193a57709acde5d290c8f83435562da42924c54cab0aa3a4f26095190` | 14 |
