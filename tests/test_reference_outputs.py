"""Reference-output tests (task 64) — the pixel-identical proof.

The expected hashes were recorded from v0.18 (the Qt core) before
the refactor, over decoded RGBA bytes — never over PNG file bytes,
because the PNG encoder changed in v0.19 while the pixels must
not. The inputs are generated deterministically here, so the test
is hermetic: any pixel difference from the v0.18 core fails loudly
with the name of the diverging case.

48 of the hashes were re-recorded at v0.49, when nearest_color
switched from squared Euclidean to the redmean approximation
(task 39) — see REDMEAN_MOVES below for the per-case proof that
each new output is the perceptually closer one. The other hashes
are still the v0.18 values.

Qt-free: runs on bare python (which also exercises the stdlib
PNG path end to end, since the inputs are built in memory and the
outputs are hashed from the buffers directly).
"""
import hashlib
import os
import random
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from pixel_forge.codeinput import (render_color_grid,  # noqa: E402
                                   render_palette_strip)
from pixel_forge.image import Image, _sample_map, argb  # noqa: E402
from pixel_forge.pixelize import downscale, pixelize_image  # noqa: E402
from pixel_forge.skins import (pixelize_texture, skin_figure,  # noqa: E402
                               skin_parts)
from pixel_forge.spritesheet import (extract_frame,  # noqa: E402
                                     frames_from_sheet, pack_frames)

# Hashes recorded from the v0.18 Qt core (see the module
# docstring). Do not regenerate from a new tree unless that
# tree's outputs were verified correct by hand first.
#
# REDMEAN MOVES (v0.49, task 39) — nearest_color switched from
# squared Euclidean to the redmean approximation, so every
# pixelize-derived output could move where two palette entries
# were near-tied. 48 hashes moved, and each was verified by hand
# before re-recording (script /tmp/verify_redmean.py, kept out of
# the tree; the subset check is /tmp/verify_subset.py):
#
# Mechanism 1 — per-pixel remap (46 of the 48): the working
# palette is unchanged, but a pixel's nearest entry differs
# under redmean. For EVERY changed pixel in these cases, the new
# color is redmean-closer to the source color (Bayer-shifted
# where dithered) than the old color was — 0 violations across
# all of them. Changed-pixel counts (changed/total):
#   two_color pico8 180/1024, grayscale 1024/1024, gameboy 260/1024
#   transparent grayscale 512/1024, gameboy 288/1024
#   photo_like image 64:146/4096, 32:18/1024, 24:7/576, 16:7/256
#     (dithered) / 8/256 (plain); pico8 32:126/1024;
#     grayscale 497/1024; gameboy 173/1024
#   noisy image 64:118/4096, 32:22/1024, 24:6/576, 16:8/256 / 7/256;
#     pico8 32:45/1024; grayscale 256/1024; gameboy 84/1024
#   gradient pico8 48:178/2304, 32:102/1024; grayscale 483/1024;
#     gameboy 170/1024
#   alpha_ramp pico8 56/1024, gameboy 304/1024
#   pixelize-rect photo_like 115/960, noisy 62/960, gradient 94/960
#   pixelize-aspect photo_like 44/1376, noisy 22/1024
#   texture:big 3749/76800, texture:capped 11280/196608
#   sheet-cut frame 1 (noisy pico8, no dither) 70/1024
# The clearest sample is two_color on grayscale: pure green
# (0,255,0) used to map to mid-gray (85,85,85) — Euclidean said
# so (43350) — and now maps to light gray (119,119,119), because
# green is the brightest channel to the eye (redmean 151697 ->
# 144734, while Euclidean rises 43350 -> 46818 — the point of
# the perceptual metric). Every pixel of that image changed.
#
# Mechanism 2 — subset change (2 cases): at 48px on pico8 with
# max_colors 8, select_palette_colors cuts the palette by votes,
# and the votes move under redmean, so the working palette
# itself changes. A pixel whose color left the subset gets the
# nearest REMAINING entry, which can be individually farther —
# so the check here is aggregate: total redmean error over the
# whole image.
#   photo_like 48:pico8:8: (95,87,79) leaves, (126,37,83)
#     enters; 584/2304 pixels changed; total redmean error
#     49,598,798 -> 46,887,271 (5.5% better).
#   noisy 48:pico8:8: (95,87,79) and (171,82,54) leave,
#     (29,43,83) and (126,37,83) enter — redmean votes 87 and
#     82 against Euclidean votes 65 and 70; 447/2304 pixels
#     changed; total redmean error 24,151,927 -> 23,558,418
#     (2.5% better). Total Euclidean error rises in both cases,
#     as expected: redmean optimizes what the eye sees, not the
#     raw channel distance.
# (gradient 48:pico8:8 kept the same 8 entries — only the vote
# order moved — so it is Mechanism 1, not 2.)
#
# The sheet:* (4) and sheet-cut:*:1 / sheet-cut:*:3 (8) hashes
# move only because sheet frames 1 and 3 moved — the packer is
# untouched. Proven: frames 0, 2, 4 are byte-identical to their
# v0.18 hashes, frame 1 matches the verified noisy-pico8 output,
# frame 3 matches the verified two_color-gameboy output.
# The downscale/upscale/skin/code hashes did not move — none of
# them calls nearest_color.
REFERENCE = {
    "code:grid": "e41145afd76f01005842fbc46d085bfe3f641484f2e8521d995754f773316a92",
    "code:strip": "6218127ba24c9686779cc9c7b67c926e900f814afdbfa9f4c5af0ca7ba52bf82",
    "downscale:100x66": "6b37ff857e3752f98d41232d8c0e8ec667ed474ca0ba7b3e5e359265a3abac4e",
    "downscale:101x67": "055c7217ef616b2445262dac442b591c8df6433bbd17507189bc5a3630228a18",
    "downscale:128x96": "ad807ba57fbbdb667757d18b582516e5d153b53c6fb7ce4d7034834dca53f3f4",
    "downscale:16x16": "8f73029703d6ad5c0c30c5b46681f4a0abb4c11be140b48249b9bf33d1cf9663",
    "downscale:17x91": "46edee185820cf6f116fefeba94c8963b5b194e02d97b7e5cba95eff336a87b1",
    "downscale:1x1": "f85bf091e2415782216b436567e71872ea61844bbebc955e84321a85762bd475",
    "downscale:200x134": "62d4c9593087641d10d7b07611db775a8aecb2c3a4a17781cc225c76cc9b0550",
    "downscale:2x2": "05d453c9400f5858f119f157ad08e863b690fa593aa8250ed18e9109c8fdc6cc",
    "downscale:32x32": "f082584bb26f2befd5eaa9c2f0dc9a92fa53d0731428f4dcbb03fc5fbcc4319f",
    "downscale:50x33": "88d78ee3f534cb2e4401cbc08c2d7700180b65264ead3b7262404ef6e2f202d0",
    "downscale:64x64": "8cc2bddc570efc4a001b47364cb653be212c345d288c148ffbc6844a78607882",
    "downscale:7x5": "995ad09db894e1055b35da689736182f864c1083203d87904980b96485381fe5",
    "downscale:91x17": "692cf74cf721c3754d9fb98143570f4797cbf3655d9269ed20553bfb38aeca7d",
    "pixelize-aspect:alpha_ramp": "01b2e25434dc55b3d09b45a73a32b78bf6875439ac2ec80e2ff20ac0aef68d51",
    "pixelize-aspect:gradient": "b93f1382d22dab62afc472241ba079a168ca9756e8d899bafc9683aad376e69b",
    "pixelize-aspect:noisy": "af2b031a6fcc8ceea81d3dc34eda28421ef6f88db4d9baf8df40bb2c3f44c117",
    "pixelize-aspect:photo_like": "6dd3b806cfa1c254e5f76f3a2e57b87b06358c352f8a0435338051ff1a0b7232",
    "pixelize-aspect:transparent": "b7bba985bd8d5f1cf4ca561efff03f7b85517e9f04108faaf4bcf3d69d10d4ff",
    "pixelize-aspect:two_color": "fd2f55a05b46278853664cdd07ee134990620bf00a6bc17c71978b8e38e6bf51",
    "pixelize-rect:alpha_ramp": "aaf8aafdf00230b5245c93fe9c21f84abe58d00f5d6a570bf21835afc72d440e",
    "pixelize-rect:gradient": "b315c77d1148f12e7b83aa7b2f16f06598bdf9cc7ed82036c060ca2bd00a1103",
    "pixelize-rect:noisy": "5d02749ce8124d0be93007a26c95fc55db59f262979737782c10d00c684ac153",
    "pixelize-rect:photo_like": "9203b3482ceeed6a2caab218f58d4f736d6e40d7c8d76d6fb75e2c261bb9422a",
    "pixelize-rect:transparent": "a29e339edef40a7851783250032ad98fa89b85d72e8de6b5198cad6f7747a29d",
    "pixelize-rect:two_color": "434e284865a41a7bfaba0520c390091e0ff00df0c73b8eae70bbaec1164931fc",
    "pixelize:alpha_ramp:16:image:16:False": "aa4bab84babd61e67994a9a2f85f9a825d06722431f501799034a17ec2268646",
    "pixelize:alpha_ramp:16:image:16:True": "aa4bab84babd61e67994a9a2f85f9a825d06722431f501799034a17ec2268646",
    "pixelize:alpha_ramp:24:image:8:True": "6364ab8d196108e45fe33e501c3dd8e62617e5ff278fde5ad9db7bfdeb833b88",
    "pixelize:alpha_ramp:32:gameboy:4:True": "db1a164b85b559264a8a6801a51f00235cedf1f2d947cdfbb15e8bf37358709f",
    "pixelize:alpha_ramp:32:grayscale:16:False": "ee7f68f4f87bc7eb1389b2dc0d5320c8c622b7b0dd2a4d5edee9378d3128c2de",
    "pixelize:alpha_ramp:32:image:16:True": "01b2e25434dc55b3d09b45a73a32b78bf6875439ac2ec80e2ff20ac0aef68d51",
    "pixelize:alpha_ramp:32:pico8:16:True": "e738624246bdef00f26868f86ad2c14f13c6cc848da471d204a97a1cef099bab",
    "pixelize:alpha_ramp:48:pico8:8:False": "eca1d738a0298ecdf2fa6aa6a57a14cc703e111d696901098e74bcc689fdf197",
    "pixelize:alpha_ramp:64:image:32:True": "cae9e68dc82c3fd207393fc7d1104f5c7b6c543d200555ad6a43bf8ea36a4223",
    "pixelize:gradient:16:image:16:False": "3b95b75e8574c1cb6b5c46262bd7bf82072a7a62c6e099d51cc1807442ce19ac",
    "pixelize:gradient:16:image:16:True": "ab1c0c86306dcfe42044eec6a3886e70229c552f0b010c9eba41aa3a640a3b45",
    "pixelize:gradient:24:image:8:True": "63e8f630e29e6e41456c5459acc1cf63bd8af374c9d272a6ce32257cb60223fb",
    "pixelize:gradient:32:gameboy:4:True": "ffdbefd5855b36764278e2252d06018e408f9becbca01a6beb28220d9e7cbf35",
    "pixelize:gradient:32:grayscale:16:False": "c953ae32ecb9491bd7d8499d3d414710e1530d941395dc4c7e1e8d4906aa00c4",
    "pixelize:gradient:32:image:16:True": "4c85637da3dd614ff3ce9c1aa7fd8a1ca4f7146f67191525c24f294bfdb9281b",
    "pixelize:gradient:32:pico8:16:True": "787b60cdd4779bbd696f42f5d1449f249ad667e221e1a7a580e042b5b731d4dc",
    "pixelize:gradient:48:pico8:8:False": "7593e85f761f20d87421f6f9c005aa4d5fac91624816b5e7ebb686b611464f83",
    "pixelize:gradient:64:image:32:True": "bf9afc005577361360461487b356b30079821d478ff308afe0639823a2e4f283",
    "pixelize:noisy:16:image:16:False": "95c68cf7347527f78b13538b533db81fb50c8475cf3d346c1d769dce7efe014c",
    "pixelize:noisy:16:image:16:True": "0902f7a0a3d56b9067b356b645e43bec3bffd40445246a1aec503322cbeba964",
    "pixelize:noisy:24:image:8:True": "f82c71388b9af7b186f133fee7a4ddba30e9413537e9ac79b7fa1583917b7280",
    "pixelize:noisy:32:gameboy:4:True": "248c71dc4f943bec5a6cff9868f521ce80f0f01f3ab16d8d318acd3781d03832",
    "pixelize:noisy:32:grayscale:16:False": "d7265e89d3e76fa285364ae9961c2512662c9d7e40d96c3dce325cd4d7fe1b42",
    "pixelize:noisy:32:image:16:True": "af2b031a6fcc8ceea81d3dc34eda28421ef6f88db4d9baf8df40bb2c3f44c117",
    "pixelize:noisy:32:pico8:16:True": "7af23538fb7e03d39d83c1932c782a17a536b4eb9d4dbcb606a36d0cdf6ae164",
    "pixelize:noisy:48:pico8:8:False": "cb597bea741ab4deecef59df80f5167d50e949d3aea13ad8de7ecef39bf3858f",
    "pixelize:noisy:64:image:32:True": "30accb33126f44bd2965ef60f6995e1a82374982281bce47a28fec58b1cbf68e",
    "pixelize:photo_like:16:image:16:False": "f479728b67ff0329c4609da6df76085931a9fa71045f63738c00f5b06b5e6b7c",
    "pixelize:photo_like:16:image:16:True": "35f09eb41b671190085231d3041c665d979a4a1fdcb387f594597c2d0b8b200e",
    "pixelize:photo_like:24:image:8:True": "7cc1ad70580b442e5b1ab758915992fbcffe7181d913e672b921aa4040ab21be",
    "pixelize:photo_like:32:gameboy:4:True": "5d81b3cc60483e271ad9c17bdcdd7eeb745709c1109d026a14b19bcda3f1b517",
    "pixelize:photo_like:32:grayscale:16:False": "3ab30e470515c393be74348b8dc02eca2b53e57d4892a8e88f5b5105796c4858",
    "pixelize:photo_like:32:image:16:True": "85bae39075807920377d7ca4761282c083aff01ea04aabd6ec6a44297079c899",
    "pixelize:photo_like:32:pico8:16:True": "493b65fccfa06f1fe312571f6fe252e78f346ad43d0d8953927f5ad16e616fc4",
    "pixelize:photo_like:48:pico8:8:False": "fb938a7d5ecb01b5252e4af6e5c88269c992367f0fa5c0d4a8fdeec5c09a4fed",
    "pixelize:photo_like:64:image:32:True": "34f2e8b9a17c12a5819dd4f6d7970b54910dc9544caa0cc0c10bbc9d2555b6ec",
    "pixelize:transparent:16:image:16:False": "fd4d44a6c7cdf61b532e15586ab1a8bc150ba6fc9233b719cd283584c2eb8f14",
    "pixelize:transparent:16:image:16:True": "fd4d44a6c7cdf61b532e15586ab1a8bc150ba6fc9233b719cd283584c2eb8f14",
    "pixelize:transparent:24:image:8:True": "bff24c8d9d7853e8cc12d1201ba2524fed64f0eb24497ac606b19a9198debdf2",
    "pixelize:transparent:32:gameboy:4:True": "bc6f7576c1ca92f8f03d829a3b7dcd1b806367cf2e0781da06a480f2f4830e61",
    "pixelize:transparent:32:grayscale:16:False": "68afc49d926bdb32e5ad5dbec0a01e1770fd1e3fa89c09736075c41d2fac1616",
    "pixelize:transparent:32:image:16:True": "b7bba985bd8d5f1cf4ca561efff03f7b85517e9f04108faaf4bcf3d69d10d4ff",
    "pixelize:transparent:32:pico8:16:True": "b1fa163314348247a32873a60ddca0f96aa4a23f7c0364a20ec86e0a0ce07cee",
    "pixelize:transparent:48:pico8:8:False": "6c9a785a31297bec97c44f8938e3c72cd501f3c1b44a29498bfea431a9215c8b",
    "pixelize:transparent:64:image:32:True": "868436c61a69badf4722fa27d1241e2a10a69896c03e9c2e04ecc3fb389c64b2",
    "pixelize:two_color:16:image:16:False": "a5b993f1738fbf45359f6c23841d2f937c0334b9ebf210fd984a5a0d59c65236",
    "pixelize:two_color:16:image:16:True": "a5b993f1738fbf45359f6c23841d2f937c0334b9ebf210fd984a5a0d59c65236",
    "pixelize:two_color:24:image:8:True": "01a10ffadec910b027896491fbf2e9bc757f441c270224e968947e211c5ae649",
    "pixelize:two_color:32:gameboy:4:True": "97cbb2b938142115fa7e33cf5e6960a05670724620575f2963f5ce765f9905b1",
    "pixelize:two_color:32:grayscale:16:False": "f18a6b8e01a467f54b5c74958c7d2a92508954eebebe5cebbadb045353535f1c",
    "pixelize:two_color:32:image:16:True": "fd2f55a05b46278853664cdd07ee134990620bf00a6bc17c71978b8e38e6bf51",
    "pixelize:two_color:32:pico8:16:True": "e0d608a5963cba5c5dcbd045f246ae880f5e674b937c34acdd7d0d444c47bcf4",
    "pixelize:two_color:48:pico8:8:False": "0e6d66f1d11dd44692956e2379d0e178274449a0d527ca56dfe8a94d975f5fdb",
    "pixelize:two_color:64:image:32:True": "7b423d481a9246c88a2b7cb437b65999af941e3c6fb6dadadec77c1841c2b92f",
    "sheet-cut0:2:0": "4c85637da3dd614ff3ce9c1aa7fd8a1ca4f7146f67191525c24f294bfdb9281b",
    "sheet-cut0:3:2": "4c85637da3dd614ff3ce9c1aa7fd8a1ca4f7146f67191525c24f294bfdb9281b",
    "sheet-cut0:5:1": "4c85637da3dd614ff3ce9c1aa7fd8a1ca4f7146f67191525c24f294bfdb9281b",
    "sheet-cut0:None:0": "4c85637da3dd614ff3ce9c1aa7fd8a1ca4f7146f67191525c24f294bfdb9281b",
    "sheet-cut:2:0:0": "4c85637da3dd614ff3ce9c1aa7fd8a1ca4f7146f67191525c24f294bfdb9281b",
    "sheet-cut:2:0:1": "94823e8dc040dfcfe92612353378dab69827014fab2af8a54c42acc0396e4dca",
    "sheet-cut:2:0:2": "b7bba985bd8d5f1cf4ca561efff03f7b85517e9f04108faaf4bcf3d69d10d4ff",
    "sheet-cut:2:0:3": "97cbb2b938142115fa7e33cf5e6960a05670724620575f2963f5ce765f9905b1",
    "sheet-cut:2:0:4": "01b2e25434dc55b3d09b45a73a32b78bf6875439ac2ec80e2ff20ac0aef68d51",
    "sheet-cut:3:2:0": "4c85637da3dd614ff3ce9c1aa7fd8a1ca4f7146f67191525c24f294bfdb9281b",
    "sheet-cut:3:2:1": "94823e8dc040dfcfe92612353378dab69827014fab2af8a54c42acc0396e4dca",
    "sheet-cut:3:2:2": "b7bba985bd8d5f1cf4ca561efff03f7b85517e9f04108faaf4bcf3d69d10d4ff",
    "sheet-cut:3:2:3": "97cbb2b938142115fa7e33cf5e6960a05670724620575f2963f5ce765f9905b1",
    "sheet-cut:3:2:4": "01b2e25434dc55b3d09b45a73a32b78bf6875439ac2ec80e2ff20ac0aef68d51",
    "sheet-cut:5:1:0": "4c85637da3dd614ff3ce9c1aa7fd8a1ca4f7146f67191525c24f294bfdb9281b",
    "sheet-cut:5:1:1": "94823e8dc040dfcfe92612353378dab69827014fab2af8a54c42acc0396e4dca",
    "sheet-cut:5:1:2": "b7bba985bd8d5f1cf4ca561efff03f7b85517e9f04108faaf4bcf3d69d10d4ff",
    "sheet-cut:5:1:3": "97cbb2b938142115fa7e33cf5e6960a05670724620575f2963f5ce765f9905b1",
    "sheet-cut:5:1:4": "01b2e25434dc55b3d09b45a73a32b78bf6875439ac2ec80e2ff20ac0aef68d51",
    "sheet-cut:None:0:0": "4c85637da3dd614ff3ce9c1aa7fd8a1ca4f7146f67191525c24f294bfdb9281b",
    "sheet-cut:None:0:1": "94823e8dc040dfcfe92612353378dab69827014fab2af8a54c42acc0396e4dca",
    "sheet-cut:None:0:2": "b7bba985bd8d5f1cf4ca561efff03f7b85517e9f04108faaf4bcf3d69d10d4ff",
    "sheet-cut:None:0:3": "97cbb2b938142115fa7e33cf5e6960a05670724620575f2963f5ce765f9905b1",
    "sheet-cut:None:0:4": "01b2e25434dc55b3d09b45a73a32b78bf6875439ac2ec80e2ff20ac0aef68d51",
    "sheet:2:0": "d87c30f9975ee171e8ab192b5760a57366f93d8239a278c950761070063bb79f",
    "sheet:3:2": "be12eaf49447687ef1692c32478b8d90a5e80943473b68eaf51049afce5e4bef",
    "sheet:5:1": "3f2bd8abf1fcd0d955c78b47c92cc084c49a9f5998b5b831876fcba3c31b9d48",
    "sheet:None:0": "1936e8328a180168d83390930ec2ef34e581dd1bf90eb3da655b5d81acd84405",
    "skin-figure:32": "f6d7aaa80971020c1a8d8ae65754d0dcb582f0b73b6f3a335eedc38d95b025d7",
    "skin-figure:64": "4dc47663281043e0861f87eeaf84d8fbf38743c8eb4e73a4cc8e12fb096512f5",
    "skin-part:32:body": "5f535e46653655353d9de25c579dd03c933c4a422edb41a3a2c9f7497f703e2e",
    "skin-part:32:hat": "445dcb18749c0300689f7af7001b75aeac4c8c3b177892fdb400e9b264ab3b42",
    "skin-part:32:head": "868a00183742a9b04054b51562fc000e1cb8d20450618e624c5410f9e7ab52a3",
    "skin-part:32:left_arm": "b5f920c3394849e2429d2e8f9947b0612cc4b7134e7ae2cb60c2c74fe1696fae",
    "skin-part:32:left_leg": "58ce794d198ea3b466c1ff2f5240871cac556d9a6c822dd5d487e97e54de1d02",
    "skin-part:32:right_arm": "d2cf532dfaab1439d87aaf6021e91efa86195fa4d5167db03ed41b81d2dd06a9",
    "skin-part:32:right_leg": "e7b76adf0d0627ad8ce817091356104eac83fff44946f86abdc16fb9cbf1d24e",
    "skin-part:64:body": "1266afeb5b8379146f7a9e11101e95318b44b64445a7ed12bf2431d5e168750d",
    "skin-part:64:hat": "f270cd4348864f96f0e6e7cac4d6263674e160b21fb6a0f8f2e9ba6a8b378879",
    "skin-part:64:head": "1de7d1a8de08c201e9b8e29ced663c8b4870f22beed338286d0abf8e97c276df",
    "skin-part:64:jacket": "9eb1840402f5da668aeb292803eef553fddf97c8d19ce9dd424b55865d269f89",
    "skin-part:64:left_arm": "50cae7150abec073eb5efb41d68bedebd7bf33cc81ed888f6700ee049a992136",
    "skin-part:64:left_leg": "e2e38a13fbecf451fe93637fbe171fc28fade63073f8455bfa371df87228ca4b",
    "skin-part:64:left_pants": "cf69b6c6626733d70282ba0fcb859f6e2523e5e29dd408d167d3731d5f76317c",
    "skin-part:64:left_sleeve": "2e71c2489b6ed63c0ff64f9b5a43a5f30dc2813336bcd0f5fa738c3632f3f568",
    "skin-part:64:right_arm": "b7a09c22bd00e7957b84a31196448a1aedc80d4220a9557371b9e0e3b31cb808",
    "skin-part:64:right_leg": "ac47ba381c00b6d9643dcf18ad6b548b6ada7948c04961309290a98f68124991",
    "skin-part:64:right_pants": "44b5682587f898d94a87443620d555b6c127bf8e0ef5d16b7f98cadb8dbbd9a2",
    "skin-part:64:right_sleeve": "8577e4a62d83fbde9d21af587a9c41a4e1c6b0ef2a2487ec963be74bd1475fb9",
    "texture:big": "ca35b51cf6488c5e5cd18095a7b1b175bf2b95c876d464e2342718d52d272a5f",
    "texture:capped": "4466ff139ef0f912c3a9b98737f555a0552d51102360bf0a06b7091822a67d62",
    "texture:small": "0363c0339e83fbf1ecdd4f1c17c673a01e14daf259bed7c6ef1e74d3d8d37f9f",
    "upscale:32x32": "6d1159bc6389d4e203e72892e0c0f455785b31dfe71cfdcc748535bfb8d2dacd",
    "upscale:33x33": "4e1061501e9b1a2e70f799b3db109ff4373515a6ce6e1e9d150f3cf0ed8e7024",
    "upscale:48x48": "ad37bb5c56ece3fedde6d5e3945455ad3dc8c54b56667da1e5128383d900e5f9",
    "upscale:64x64": "5b4a5ca66560129616719ed99852c04c94a9415c4750080d1cce1715908b9060"
}


def _digest(image):
    """SHA-256 over dims + RGBA bytes per pixel."""
    out = bytearray()
    out += image.width().to_bytes(4, "big")
    out += image.height().to_bytes(4, "big")
    out += image.data
    return hashlib.sha256(bytes(out)).hexdigest()


def _img(width, height, fn):
    image = Image(width, height)
    for y in range(height):
        for x in range(width):
            r, g, b, a = fn(x, y)
            image.setPixel(x, y, argb(r, g, b, a))
    return image


def _compute_all():
    """Recompute every reference output from the current tree."""
    refs = {}

    gradient = _img(200, 100, lambda x, y: (
        x * 255 // 199, y * 255 // 99, 128, 255))
    transparent = _img(64, 64, lambda x, y: (
        (255, 0, 0, 255) if x < 32 else (0, 0, 0, 0)))
    two_color = _img(64, 64, lambda x, y: (
        (0, 255, 0, 255) if (x < 16 and y < 16)
        else (139, 0, 0, 255)))
    rng = random.Random(42)
    rects = [(rng.randrange(128), rng.randrange(128),
              rng.randrange(1, 12), rng.randrange(1, 12),
              (rng.randrange(256), rng.randrange(256),
               rng.randrange(256))) for _ in range(300)]
    noisy = _img(128, 128, lambda x, y: (0, 0, 0, 255))
    for rx, ry, rw, rh, (r, g, b) in rects:
        for yy in range(ry, min(128, ry + rh)):
            for xx in range(rx, min(128, rx + rw)):
                noisy.setPixel(xx, yy, argb(r, g, b, 255))
    photo_like = _img(320, 240, lambda x, y: (
        (x * 7 + y * 13) % 256, (x * 3 + y * 11) % 256,
        (x * 5 + y * 17) % 256, 255))
    alpha_ramp = _img(48, 48, lambda x, y: (
        200, 100, 50, (x * 255) // 47))

    inputs = {"gradient": gradient, "transparent": transparent,
              "two_color": two_color, "noisy": noisy,
              "photo_like": photo_like, "alpha_ramp": alpha_ramp}

    combos = [(16, "image", 16, True), (16, "image", 16, False),
              (32, "image", 16, True), (32, "pico8", 16, True),
              (32, "gameboy", 4, True), (32, "grayscale", 16, False),
              (64, "image", 32, True), (48, "pico8", 8, False),
              (24, "image", 8, True)]
    for name, src in inputs.items():
        for size, palette, colors, dither in combos:
            out = pixelize_image(src, width=size, palette=palette,
                                 max_colors=colors, dither=dither)
            refs["pixelize:%s:%d:%s:%d:%s" % (
                name, size, palette, colors, dither)] = _digest(out)
        out = pixelize_image(src, width=64, height=32,
                             keep_aspect=True, palette="image",
                             max_colors=16, dither=True)
        refs["pixelize-aspect:%s" % name] = _digest(out)
        out = pixelize_image(src, width=40, height=24,
                             palette="pico8", max_colors=16,
                             dither=False)
        refs["pixelize-rect:%s" % name] = _digest(out)

    probe = _img(101, 67, lambda x, y: (x % 256, y % 256, 0, 255))
    for dw, dh in [(1, 1), (2, 2), (7, 5), (16, 16), (32, 32),
                   (50, 33), (64, 64), (100, 66), (101, 67),
                   (128, 96), (200, 134), (17, 91), (91, 17)]:
        out = downscale(probe, dw, dh)
        refs["downscale:%dx%d" % (dw, dh)] = _digest(out)
    tiny = _img(16, 16, lambda x, y: ((x * 16) % 256,
                                      (y * 16) % 256, 128, 255))
    for dw, dh in [(32, 32), (48, 48), (64, 64), (33, 33)]:
        refs["upscale:%dx%d" % (dw, dh)] = _digest(
            downscale(tiny, dw, dh))

    frames = [pixelize_image(gradient, width=32, palette="image",
                             max_colors=16, dither=True),
              pixelize_image(noisy, width=32, palette="pico8",
                             max_colors=16, dither=False),
              pixelize_image(transparent, width=32, palette="image",
                             max_colors=16, dither=True),
              pixelize_image(two_color, width=32, palette="gameboy",
                             max_colors=4, dither=True),
              pixelize_image(alpha_ramp, width=32, palette="image",
                             max_colors=16, dither=True)]
    for cols, padding in [(None, 0), (2, 0), (3, 2), (5, 1)]:
        sheet, layout = pack_frames(frames, cols=cols,
                                    padding=padding)
        refs["sheet:%s:%d" % (cols, padding)] = _digest(sheet)
        meta = {"frames": [{"x": x, "y": y, "w": 32, "h": 32}
                           for (x, y) in layout["positions"]]}
        for i, frame in enumerate(frames_from_sheet(sheet, meta)):
            refs["sheet-cut:%s:%d:%d" % (cols, padding, i)] = \
                _digest(frame)
        refs["sheet-cut0:%s:%d" % (cols, padding)] = _digest(
            extract_frame(sheet, meta, 0))

    skin64 = _img(64, 64, lambda x, y: ((x * 4) % 256, (y * 4) % 256,
                                        (x + y) % 256, 255))
    skin32 = _img(64, 32, lambda x, y: ((x * 4) % 256, (y * 8) % 256,
                                        (x * y) % 256, 255))
    for y in range(8, 16):
        for x in range(40, 48):
            skin64.setPixel(x, y, argb(0, 0, 0, 0))
    for name, skin in (("64", skin64), ("32", skin32)):
        for part_name, part in skin_parts(skin).items():
            refs["skin-part:%s:%s" % (name, part_name)] = \
                _digest(part)
        refs["skin-figure:%s" % name] = _digest(skin_figure(skin))

    refs["texture:small"] = _digest(pixelize_texture(
        _img(16, 16, lambda x, y: ((x * 16) % 256, (y * 16) % 256,
                                   200, 255))))
    refs["texture:big"] = _digest(pixelize_texture(photo_like))
    big = _img(1024, 768, lambda x, y: ((x * 3) % 256, (y * 5) % 256,
                                        (x + y) % 256, 255))
    refs["texture:capped"] = _digest(pixelize_texture(big))

    grid = [[(255, 0, 0, 255), None, (0, 255, 0, 255)],
            [None, (0, 0, 255, 128), (255, 255, 0, 255)],
            [(10, 20, 30, 255), (40, 50, 60, 0), (70, 80, 90, 255)]]
    refs["code:grid"] = _digest(render_color_grid(grid))
    refs["code:strip"] = _digest(render_palette_strip(
        [(255, 0, 0, 255), (0, 255, 0, 255), (0, 0, 255, 255),
         (255, 255, 0, 0)]))
    return refs


class ReferenceOutputTests(unittest.TestCase):
    def test_all_reference_outputs_match_v018(self):
        computed = _compute_all()
        self.assertEqual(set(computed), set(REFERENCE),
                         "reference set changed — a case was added "
                         "or dropped")
        for key, expected in sorted(REFERENCE.items()):
            with self.subTest(case=key):
                self.assertEqual(computed[key], expected)


class SampleMapTests(unittest.TestCase):
    """The downscale sampling formula, pinned on the cases that
    separate it from the naive formulas (probed against
    QImage.scaled — see image._sample_map)."""

    def test_downscale_boundaries(self):
        self.assertEqual(_sample_map(101, 32)[:8],
                         [1, 4, 7, 11, 14, 17, 20, 23])
        self.assertEqual(_sample_map(100, 3), [16, 49, 83])
        self.assertEqual(_sample_map(64, 48)[:8],
                         [0, 1, 3, 4, 5, 7, 8, 9])
        self.assertEqual(_sample_map(94, 1), [46])

    def test_identity_and_single(self):
        self.assertEqual(_sample_map(64, 64), list(range(64)))
        self.assertEqual(_sample_map(1, 1), [0])
        self.assertEqual(_sample_map(1, 5), [0, 0, 0, 0, 0])
