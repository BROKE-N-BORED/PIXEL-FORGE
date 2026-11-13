"""Reference-output tests (task 64) — the pixel-identical proof.

The expected hashes were recorded from v0.18 (the Qt core) before
the refactor, over decoded RGBA bytes — never over PNG file bytes,
because the PNG encoder changed in v0.19 while the pixels must
not. The inputs are generated deterministically here, so the test
is hermetic: any pixel difference from the v0.18 core fails loudly
with the name of the diverging case.

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
    "pixelize-aspect:noisy": "e1e6effea409ccb59878c6d9d04b36be09c64e89cf19a2dc363f91243b1088b3",
    "pixelize-aspect:photo_like": "d40746d46b1021714b961c913370fe0ff4de72937e42a9699e2e5c51315e7083",
    "pixelize-aspect:transparent": "b7bba985bd8d5f1cf4ca561efff03f7b85517e9f04108faaf4bcf3d69d10d4ff",
    "pixelize-aspect:two_color": "fd2f55a05b46278853664cdd07ee134990620bf00a6bc17c71978b8e38e6bf51",
    "pixelize-rect:alpha_ramp": "aaf8aafdf00230b5245c93fe9c21f84abe58d00f5d6a570bf21835afc72d440e",
    "pixelize-rect:gradient": "593c1a5ffc6d88bbb323fb141e3de7addd28d0817625d000b70ba8da08aa0073",
    "pixelize-rect:noisy": "04c348d8840d55dd84a3242e533abafd7532d7e94072f4f72981a554e7cc410e",
    "pixelize-rect:photo_like": "66b30abf5f61d7b63ee59b975787ab4cddd32af50aea44a4f8967a387594507c",
    "pixelize-rect:transparent": "a29e339edef40a7851783250032ad98fa89b85d72e8de6b5198cad6f7747a29d",
    "pixelize-rect:two_color": "434e284865a41a7bfaba0520c390091e0ff00df0c73b8eae70bbaec1164931fc",
    "pixelize:alpha_ramp:16:image:16:False": "aa4bab84babd61e67994a9a2f85f9a825d06722431f501799034a17ec2268646",
    "pixelize:alpha_ramp:16:image:16:True": "aa4bab84babd61e67994a9a2f85f9a825d06722431f501799034a17ec2268646",
    "pixelize:alpha_ramp:24:image:8:True": "6364ab8d196108e45fe33e501c3dd8e62617e5ff278fde5ad9db7bfdeb833b88",
    "pixelize:alpha_ramp:32:gameboy:4:True": "0ae0c92fcbcd3810df440cd7fc37c57d9d7bb7dc97a6f0aee97a4447b4bf58d7",
    "pixelize:alpha_ramp:32:grayscale:16:False": "ee7f68f4f87bc7eb1389b2dc0d5320c8c622b7b0dd2a4d5edee9378d3128c2de",
    "pixelize:alpha_ramp:32:image:16:True": "01b2e25434dc55b3d09b45a73a32b78bf6875439ac2ec80e2ff20ac0aef68d51",
    "pixelize:alpha_ramp:32:pico8:16:True": "fed6141432e889e09275be77c32a7f52d5edd598f81da484a0cf669f6ab50c58",
    "pixelize:alpha_ramp:48:pico8:8:False": "eca1d738a0298ecdf2fa6aa6a57a14cc703e111d696901098e74bcc689fdf197",
    "pixelize:alpha_ramp:64:image:32:True": "cae9e68dc82c3fd207393fc7d1104f5c7b6c543d200555ad6a43bf8ea36a4223",
    "pixelize:gradient:16:image:16:False": "3b95b75e8574c1cb6b5c46262bd7bf82072a7a62c6e099d51cc1807442ce19ac",
    "pixelize:gradient:16:image:16:True": "ab1c0c86306dcfe42044eec6a3886e70229c552f0b010c9eba41aa3a640a3b45",
    "pixelize:gradient:24:image:8:True": "63e8f630e29e6e41456c5459acc1cf63bd8af374c9d272a6ce32257cb60223fb",
    "pixelize:gradient:32:gameboy:4:True": "036c58904f311dda632ed8fa3b0ee9d39174615fc0fff1d640d687b5e248c8fd",
    "pixelize:gradient:32:grayscale:16:False": "3296db787e85ab0ac0a6b7913049e25d9e371f17000a9b454a8587d321c53fde",
    "pixelize:gradient:32:image:16:True": "4c85637da3dd614ff3ce9c1aa7fd8a1ca4f7146f67191525c24f294bfdb9281b",
    "pixelize:gradient:32:pico8:16:True": "87807c99f48aa49971bc87df2cf4e59bcfcbb423425ffe8d061b638c208240da",
    "pixelize:gradient:48:pico8:8:False": "cdbc336b4f7ddd6de2b897be3bf0cb528390b4695e95db3567374bd78af983aa",
    "pixelize:gradient:64:image:32:True": "bf9afc005577361360461487b356b30079821d478ff308afe0639823a2e4f283",
    "pixelize:noisy:16:image:16:False": "eced897a5b56a1974a315959049139339f2abc370d213197c2dbcdf848f4f122",
    "pixelize:noisy:16:image:16:True": "4fd348af0db01626feaee1536485c60b39a9c3afd85047fbfd586ffbf82bdf5f",
    "pixelize:noisy:24:image:8:True": "c3ded38b2230d669e04f6e503394c492abd3e64ad258cd11d317ac99fd827cd3",
    "pixelize:noisy:32:gameboy:4:True": "1435da5ef5eb7803524803d934a792975ea82cc3ee2a37c9cb8e0ccc82a75503",
    "pixelize:noisy:32:grayscale:16:False": "5f01165c21794bed392eadb255fc619ff745ad208b91c339877fdc1db20e75c4",
    "pixelize:noisy:32:image:16:True": "e1e6effea409ccb59878c6d9d04b36be09c64e89cf19a2dc363f91243b1088b3",
    "pixelize:noisy:32:pico8:16:True": "cf87398faac46e9d5073f8895ecd3fb76584a983fb878479fd24cf8471ffd7e6",
    "pixelize:noisy:48:pico8:8:False": "d6d81de627a4f7c6d92164b8ab5b59c562f691504e8774613fa770d53c913418",
    "pixelize:noisy:64:image:32:True": "88ff6840339c37015337ed9aa7239948fef12912bc1255ca3ae3f07cb583e8a2",
    "pixelize:photo_like:16:image:16:False": "132ae8516c2558fc0edc75f10a06a35c655fe250036f5473e400a64c78a95528",
    "pixelize:photo_like:16:image:16:True": "d03ebe67dd179192fa14c63a68e4cb11d283efd6b5c1d7d28d345320ab2a1491",
    "pixelize:photo_like:24:image:8:True": "129209f2b9ead0e79266bf268c1b52c941c42b12caaae6bdb51c11b4b254997e",
    "pixelize:photo_like:32:gameboy:4:True": "8dba8f9d7f01060edbc4f4d49cb05ddb4dfeb8b147fc4c4fc7b6c880538b4176",
    "pixelize:photo_like:32:grayscale:16:False": "67aa6714f1752ab34b4606c015025590cf8ed3cf38b40d318b3d9882cef4bd33",
    "pixelize:photo_like:32:image:16:True": "bd68145d6f65b465d2789c04954d6b80abc1dfe60336b915752859a3b1a0e151",
    "pixelize:photo_like:32:pico8:16:True": "d2ee6e358999b2d9e85e093b49f86be060bc0b278370dfc6c654e9ea3a5ce276",
    "pixelize:photo_like:48:pico8:8:False": "2e271c276a0a2c456ac1f996d39ed7082690164c7ce5ab6947b1439f81a9b8f6",
    "pixelize:photo_like:64:image:32:True": "2d75734025845445bfdb051e9f2bd259598a3bf2511a18b48b77bef932d5ec60",
    "pixelize:transparent:16:image:16:False": "fd4d44a6c7cdf61b532e15586ab1a8bc150ba6fc9233b719cd283584c2eb8f14",
    "pixelize:transparent:16:image:16:True": "fd4d44a6c7cdf61b532e15586ab1a8bc150ba6fc9233b719cd283584c2eb8f14",
    "pixelize:transparent:24:image:8:True": "bff24c8d9d7853e8cc12d1201ba2524fed64f0eb24497ac606b19a9198debdf2",
    "pixelize:transparent:32:gameboy:4:True": "4d75805f65dcc90cab7799b85d2ff5ec327a8b7970e93a05fd72e4e9b9eda085",
    "pixelize:transparent:32:grayscale:16:False": "07447cb891b317c1a8298d744891016e506dce0e3888b24836ca41f6fa208358",
    "pixelize:transparent:32:image:16:True": "b7bba985bd8d5f1cf4ca561efff03f7b85517e9f04108faaf4bcf3d69d10d4ff",
    "pixelize:transparent:32:pico8:16:True": "b1fa163314348247a32873a60ddca0f96aa4a23f7c0364a20ec86e0a0ce07cee",
    "pixelize:transparent:48:pico8:8:False": "6c9a785a31297bec97c44f8938e3c72cd501f3c1b44a29498bfea431a9215c8b",
    "pixelize:transparent:64:image:32:True": "868436c61a69badf4722fa27d1241e2a10a69896c03e9c2e04ecc3fb389c64b2",
    "pixelize:two_color:16:image:16:False": "a5b993f1738fbf45359f6c23841d2f937c0334b9ebf210fd984a5a0d59c65236",
    "pixelize:two_color:16:image:16:True": "a5b993f1738fbf45359f6c23841d2f937c0334b9ebf210fd984a5a0d59c65236",
    "pixelize:two_color:24:image:8:True": "01a10ffadec910b027896491fbf2e9bc757f441c270224e968947e211c5ae649",
    "pixelize:two_color:32:gameboy:4:True": "2b4fbb92cc275d787ebe67a1eb47f8a774fee2da45538a1f3147acd099d1037c",
    "pixelize:two_color:32:grayscale:16:False": "60bfe9fbdcf2d967f588cb6161b9717a98d2a1ff7e035db3f8a1d10e41f187ec",
    "pixelize:two_color:32:image:16:True": "fd2f55a05b46278853664cdd07ee134990620bf00a6bc17c71978b8e38e6bf51",
    "pixelize:two_color:32:pico8:16:True": "9f699e2019c423c93583cde59e30ab1e06957eec032fc0de8316c419920c1173",
    "pixelize:two_color:48:pico8:8:False": "0e6d66f1d11dd44692956e2379d0e178274449a0d527ca56dfe8a94d975f5fdb",
    "pixelize:two_color:64:image:32:True": "7b423d481a9246c88a2b7cb437b65999af941e3c6fb6dadadec77c1841c2b92f",
    "sheet-cut0:2:0": "4c85637da3dd614ff3ce9c1aa7fd8a1ca4f7146f67191525c24f294bfdb9281b",
    "sheet-cut0:3:2": "4c85637da3dd614ff3ce9c1aa7fd8a1ca4f7146f67191525c24f294bfdb9281b",
    "sheet-cut0:5:1": "4c85637da3dd614ff3ce9c1aa7fd8a1ca4f7146f67191525c24f294bfdb9281b",
    "sheet-cut0:None:0": "4c85637da3dd614ff3ce9c1aa7fd8a1ca4f7146f67191525c24f294bfdb9281b",
    "sheet-cut:2:0:0": "4c85637da3dd614ff3ce9c1aa7fd8a1ca4f7146f67191525c24f294bfdb9281b",
    "sheet-cut:2:0:1": "37c5cd61b1505e161bc5876c31fac7fb9e62c8b6b6e9900c20ed5234657ce863",
    "sheet-cut:2:0:2": "b7bba985bd8d5f1cf4ca561efff03f7b85517e9f04108faaf4bcf3d69d10d4ff",
    "sheet-cut:2:0:3": "2b4fbb92cc275d787ebe67a1eb47f8a774fee2da45538a1f3147acd099d1037c",
    "sheet-cut:2:0:4": "01b2e25434dc55b3d09b45a73a32b78bf6875439ac2ec80e2ff20ac0aef68d51",
    "sheet-cut:3:2:0": "4c85637da3dd614ff3ce9c1aa7fd8a1ca4f7146f67191525c24f294bfdb9281b",
    "sheet-cut:3:2:1": "37c5cd61b1505e161bc5876c31fac7fb9e62c8b6b6e9900c20ed5234657ce863",
    "sheet-cut:3:2:2": "b7bba985bd8d5f1cf4ca561efff03f7b85517e9f04108faaf4bcf3d69d10d4ff",
    "sheet-cut:3:2:3": "2b4fbb92cc275d787ebe67a1eb47f8a774fee2da45538a1f3147acd099d1037c",
    "sheet-cut:3:2:4": "01b2e25434dc55b3d09b45a73a32b78bf6875439ac2ec80e2ff20ac0aef68d51",
    "sheet-cut:5:1:0": "4c85637da3dd614ff3ce9c1aa7fd8a1ca4f7146f67191525c24f294bfdb9281b",
    "sheet-cut:5:1:1": "37c5cd61b1505e161bc5876c31fac7fb9e62c8b6b6e9900c20ed5234657ce863",
    "sheet-cut:5:1:2": "b7bba985bd8d5f1cf4ca561efff03f7b85517e9f04108faaf4bcf3d69d10d4ff",
    "sheet-cut:5:1:3": "2b4fbb92cc275d787ebe67a1eb47f8a774fee2da45538a1f3147acd099d1037c",
    "sheet-cut:5:1:4": "01b2e25434dc55b3d09b45a73a32b78bf6875439ac2ec80e2ff20ac0aef68d51",
    "sheet-cut:None:0:0": "4c85637da3dd614ff3ce9c1aa7fd8a1ca4f7146f67191525c24f294bfdb9281b",
    "sheet-cut:None:0:1": "37c5cd61b1505e161bc5876c31fac7fb9e62c8b6b6e9900c20ed5234657ce863",
    "sheet-cut:None:0:2": "b7bba985bd8d5f1cf4ca561efff03f7b85517e9f04108faaf4bcf3d69d10d4ff",
    "sheet-cut:None:0:3": "2b4fbb92cc275d787ebe67a1eb47f8a774fee2da45538a1f3147acd099d1037c",
    "sheet-cut:None:0:4": "01b2e25434dc55b3d09b45a73a32b78bf6875439ac2ec80e2ff20ac0aef68d51",
    "sheet:2:0": "14f148bbbba68d4d2d218e74d7adc90a24c1fcbcaadedf36b4a2ccd41c292d31",
    "sheet:3:2": "6511dfcb07b694fc83afd85fdc0a1f505b040e753cba50c9fb1044e7dd380264",
    "sheet:5:1": "e0b80201d162b0f9e4ded51978c27246fc25d37590cad5585cf1dac9f2250fdf",
    "sheet:None:0": "a746bb71a37a76fe6f6ca3a6cff28fb43241b6b7c6dd52e02c1bcca7b90b39ce",
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
    "texture:big": "deae9e274ee9f2252b144184e7d3803db0866c7b020958a6b4a4ae127f2a0d5e",
    "texture:capped": "8ea13b7ba3d33b360e89e977815a290625853f71a77ef848acd281e8de5914b0",
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
