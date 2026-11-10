"""加密紙本備份：格式 JTPB v1 的核心模組（瀏覽器端）。

* 瀏覽器模組本身的行為（`tests/js/pb_core_test.mjs`，在 node 裡跑同一份檔案）。
* **跨實作交叉驗證**：這裡另寫一份只在測試裡的解碼器 / 編碼器（`cryptography` 的 AES-GCM＋
  獨立的 GF(2^8) 插值，跟產品用的查表法不同算法），瀏覽器模組做出來的要解得開，
  這裡做出來的瀏覽器模組也要解得開 —— 格式寫錯一邊、兩邊一起錯的機會才小。
* 固定測試向量：格式上線後永遠要解得開（`app/tools/paper_backup/vectors-v1.json`）。
* 內建的第三方檔案一個位元組都不能動（跟 manifest 的 SHA-256 比）。
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import secrets
import shutil
import struct
import subprocess
import time
import zlib
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
NODE = shutil.which("node")
pytestmark = pytest.mark.skipif(not NODE, reason="沒有 node")

VECTORS = ROOT / "app" / "tools" / "paper_backup" / "vectors-v1.json"


def _node(*args: str, timeout: int = 180) -> str:
    r = subprocess.run([NODE, "--no-warnings", "--experimental-loader", "./tests/js/pb_loader.mjs", *args],
                       cwd=ROOT, capture_output=True, text=True, timeout=timeout)
    assert r.returncode == 0, r.stderr[-2000:]
    return r.stdout


# ---------------- 測試專用的獨立實作（不進產品） ----------------

B45 = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ $%*+-./:"


def b45_decode(s: str) -> bytes:
    out = bytearray()
    for i in range(0, len(s), 3):
        chunk = [B45.index(c) for c in s[i:i + 3]]
        if len(chunk) == 3:
            n = chunk[0] + chunk[1] * 45 + chunk[2] * 2025
            assert n <= 0xFFFF
            out += n.to_bytes(2, "big")
        else:
            assert len(chunk) == 2
            n = chunk[0] + chunk[1] * 45
            assert n <= 0xFF
            out.append(n)
    return bytes(out)


def b45_encode(b: bytes) -> str:
    s = []
    for i in range(0, len(b) - 1, 2):
        n = b[i] * 256 + b[i + 1]
        c, n = n % 45, n // 45
        d, e = n % 45, n // 45
        s += [B45[c], B45[d], B45[e]]
    if len(b) % 2:
        s += [B45[b[-1] % 45], B45[b[-1] // 45]]
    return "".join(s)


def gf_mul(a: int, b: int) -> int:
    """GF(2^8)、多項式 x^8+x^4+x^3+x+1（0x11B）—— 用逐位元乘法，跟產品的查表法不同。"""
    p = 0
    while b:
        if b & 1:
            p ^= a
        a <<= 1
        if a & 0x100:
            a ^= 0x11B
        b >>= 1
    return p


def gf_inv(a: int) -> int:
    assert a
    r = 1
    for _ in range(254):          # a^254 = a^-1
        r = gf_mul(r, a)
    return r


def shamir_combine(shares: list[bytes]) -> bytes:
    xs = [s[-1] for s in shares]
    assert len(set(xs)) == len(xs)
    out = bytearray()
    for i in range(len(shares[0]) - 1):
        acc = 0
        for j, sj in enumerate(shares):
            num, den = 1, 1
            for k, xk in enumerate(xs):
                if k != j:
                    num = gf_mul(num, xk)               # (0 - xk) = xk
                    den = gf_mul(den, xs[j] ^ xk)
            acc ^= gf_mul(sj[i], gf_mul(num, gf_inv(den)))
        out.append(acc)
    return bytes(out)


def shamir_split(secret: bytes, n: int, t: int) -> list[bytes]:
    xs = secrets.SystemRandom().sample(range(1, 256), n)
    shares = [bytearray() for _ in range(n)]
    for byte in secret:
        coeffs = [byte] + [secrets.randbelow(256) for _ in range(t - 2)] + [1 + secrets.randbelow(255)]
        for idx, x in enumerate(xs):
            y, xp = 0, 1
            for c in coeffs:
                y ^= gf_mul(c, xp)
                xp = gf_mul(xp, x)
            shares[idx].append(y)
    return [bytes(s) + bytes([x]) for s, x in zip(shares, xs)]


def parse_record(rec: bytes) -> dict:
    body, crc = rec[:-4], int.from_bytes(rec[-4:], "big")
    assert zlib.crc32(body) == crc, "crc"
    assert body[:2] == b"PB" and body[2] == 1
    bid = body[4:12]
    if body[3] == 1:
        idx, total = struct.unpack(">HH", body[12:16])
        return {"type": 1, "id": bid, "idx": idx, "total": total, "data": body[16:]}
    assert body[3] == 2 and len(rec) == 52
    return {"type": 2, "id": bid, "t": body[12], "n": body[13], "no": body[14], "share": body[15:48]}


def independent_restore(main_b45: list[str], share_b45: list[str]) -> dict:
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    blocks = {}
    shares = []
    for s in main_b45:
        r = parse_record(b45_decode(s))
        blocks[r["idx"]] = r
    total = next(iter(blocks.values()))["total"]
    assert sorted(blocks) == list(range(total))
    main = b"".join(blocks[i]["data"] for i in range(total))
    for s in share_b45:
        shares.append(parse_record(b45_decode(s)))
    h = main[:34]
    assert h[:4] == b"JTPB" and h[4] == 1 and h[6] == 1
    flags, bid, t, n, nonce = h[7], h[8:16], h[16], h[17], h[18:30]
    ct_len = int.from_bytes(h[30:34], "big")
    ct = main[34:]
    assert len(ct) == ct_len and all(s["id"] == bid and s["t"] == t for s in shares)
    key = shamir_combine([s["share"] for s in shares[:t]])
    plain = AESGCM(key).decrypt(nonce, ct, h)
    env = zlib.decompress(plain) if flags & 1 else plain
    o = 0
    kind = env[o]; o += 1
    created = int.from_bytes(env[o:o + 4], "big"); o += 4
    nl = int.from_bytes(env[o:o + 2], "big"); o += 2
    name = env[o:o + nl].decode(); o += nl
    ml = env[o]; o += 1
    mime = env[o:o + ml].decode(); o += ml
    cl = int.from_bytes(env[o:o + 4], "big"); o += 4
    content = env[o:o + cl]; o += cl
    assert env[o:o + 32] == hashlib.sha256(content).digest() and o + 32 == len(env)
    return {"kind": kind, "created": created, "name": name, "mime": mime, "content": content}


def independent_create(content: bytes, *, name: str, t: int, n: int, chunk: int = 264) -> dict:
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    nm = name.encode()
    env = (bytes([2]) + int(time.time()).to_bytes(4, "big") + len(nm).to_bytes(2, "big") + nm
           + bytes([0]) + len(content).to_bytes(4, "big") + content + hashlib.sha256(content).digest())
    comp = zlib.compress(env)
    flags, plain = (1, comp) if len(comp) < len(env) else (0, env)
    key, nonce, bid = os.urandom(32), os.urandom(12), os.urandom(8)
    header = b"JTPB" + bytes([1, 0, 1, flags]) + bid + bytes([t, n]) + nonce + (len(plain) + 16).to_bytes(4, "big")
    main = header + AESGCM(key).encrypt(nonce, plain, header)
    total = -(-len(main) // chunk)
    recs = []
    for i in range(total):
        body = b"PB" + bytes([1, 1]) + bid + struct.pack(">HH", i, total) + main[i * chunk:(i + 1) * chunk]
        recs.append(b45_encode(body + zlib.crc32(body).to_bytes(4, "big")))
    shares = []
    for no, s in enumerate(shamir_split(key, n, t), 1):
        body = b"PB" + bytes([1, 2]) + bid + bytes([t, n, no]) + s
        shares.append(b45_encode(body + zlib.crc32(body).to_bytes(4, "big")))
    return {"main": recs, "shares": shares}


# ---------------- 測試 ----------------

def test_browser_module_behaviour():
    results = json.loads(_node("tests/js/pb_core_test.mjs", timeout=300))
    bad = [r for r in results if not r["ok"]]
    assert len(results) >= 20 and not bad, bad


def _make(**p) -> dict:
    p["content_b64"] = base64.b64encode(p.pop("content")).decode()
    return json.loads(_node("tests/js/pb_cli.mjs", "make", json.dumps(p)))


def test_independent_decoder_opens_what_the_browser_module_makes():
    secret = "資料庫密碼：correct horse battery staple".encode()
    b = _make(kind=1, name="正式機", content=secret, t=3, n=5, density="medium")
    assert len(b["shares"]) == 5 and b["backupIdText"].count("-") == 2
    for pick in ([0, 1, 2], [4, 2, 0], [1, 3, 4]):
        env = independent_restore(b["main"], [b["shares"][i] for i in pick])
        assert env["content"] == secret and env["name"] == "正式機" and env["kind"] == 1
    # 只有 T-1 份：獨立實作也解不開（AES-GCM 驗證失敗）
    from cryptography.exceptions import InvalidTag
    with pytest.raises((InvalidTag, AssertionError)):
        independent_restore(b["main"], b["shares"][:2] + [b["shares"][2].replace("A", "B", 1)])


def test_independent_decoder_handles_uncompressed_binary():
    blob = os.urandom(3000)
    b = _make(kind=2, name="key.bin", mime="application/octet-stream", content=blob, t=2, n=2, density="high")
    assert b["compressed"] is False
    env = independent_restore(b["main"], b["shares"])
    assert env["content"] == blob and env["mime"] == "application/octet-stream"


def test_browser_module_opens_what_the_independent_encoder_makes(tmp_path):
    content = ("跨實作測試 " * 50).encode()
    made = independent_create(content, name="反向.txt", t=2, n=4)
    f = tmp_path / "in.json"
    f.write_text(json.dumps({"main": made["main"], "shares": [made["shares"][3], made["shares"][1]]}), encoding="utf-8")
    out = json.loads(_node("tests/js/pb_cli.mjs", "restore", str(f)))
    assert out["ok"], out
    assert base64.b64decode(out["content_b64"]) == content and out["name"] == "反向.txt"
    # 把一份分片換成另一組的：瀏覽器端要拒絕，不可以解出東西
    other = independent_create(content, name="x", t=2, n=4)
    f.write_text(json.dumps({"main": made["main"], "shares": [made["shares"][0], other["shares"][1]]}), encoding="utf-8")
    out = json.loads(_node("tests/js/pb_cli.mjs", "restore", str(f)))
    assert out["ok"] is False


def test_fixed_vectors_still_decode_both_ways(tmp_path):
    v = json.loads(VECTORS.read_text(encoding="utf-8"))
    assert v["format"] == "JTPB v1"
    for case in v["cases"]:
        want = base64.b64decode(case["content_b64"])
        env = independent_restore(case["main"], [case["shares"][i] for i in case["use_shares"]])
        assert env["content"] == want and env["name"] == case["name"]
        f = tmp_path / "v.json"
        f.write_text(json.dumps({"main": case["main"], "shares": [], "codes": [case["codes"][i] for i in case["use_shares"]]}),
                     encoding="utf-8")
        out = json.loads(_node("tests/js/pb_cli.mjs", "restore", str(f)))
        assert out["ok"] and base64.b64decode(out["content_b64"]) == want, (case["name"], out)


def test_vendored_third_party_files_are_byte_for_byte_unchanged():
    m = json.loads((ROOT / "static" / "vendor" / "paper-backup-vendor.json").read_text(encoding="utf-8"))
    assert {p["name"]: p["version"] for p in m["packages"]} == {"shamir-secret-sharing": "0.0.3", "qrcode-generator": "2.0.4"}
    for pkg in m["packages"]:
        for rel, digest in pkg["files"].items():
            data = (ROOT / rel).read_bytes()
            assert hashlib.sha256(data).hexdigest() == digest, rel


# ---------------- PDF：產出、解析、掃回來 ----------------

def _render_and_scan(tmp_path, pdf: Path, dpi: int = 200) -> list[list[str]]:
    import fitz
    from PIL import Image
    items = []
    with fitz.open(pdf) as doc:
        for i, page in enumerate(doc):
            pix = page.get_pixmap(dpi=dpi, alpha=False)
            img = Image.frombytes("RGB", (pix.width, pix.height), pix.samples).convert("RGBA")
            f = tmp_path / f"{pdf.stem}-{i}.rgba"
            f.write_bytes(img.tobytes())
            items.append({"file": str(f), "w": pix.width, "h": pix.height})
    lst = tmp_path / f"{pdf.stem}-scan.json"
    lst.write_text(json.dumps(items), encoding="utf-8")
    return json.loads(_node("tests/js/pb_cli.mjs", "scan", str(lst), timeout=600))


def _pdf_set(tmp_path, **p) -> dict:
    p["content_b64"] = base64.b64encode(p.pop("content")).decode()
    p["out"] = str(tmp_path)
    return json.loads(_node("tests/js/pb_cli.mjs", "pdf", json.dumps(p), timeout=300))


@pytest.mark.parametrize("density", ["low", "medium", "high"])
def test_printed_main_document_scans_back_completely(tmp_path, density):
    import fitz
    content = os.urandom(2600 if density != "low" else 1300)         # 不可壓縮 → 一頁以上的區塊
    made = _pdf_set(tmp_path, kind=2, name="x.bin", content=content, t=2, n=3, density=density)
    pdf = tmp_path / "main.pdf"
    with fitz.open(pdf) as doc:
        assert doc.page_count == 1 + -(-len(made["main"]) // 12)
        assert doc.metadata["title"].startswith("JTPB ") and "x.bin" not in json.dumps(doc.metadata)
        for page in doc:
            assert not list(page.links()) and not list(page.annots() or [])
        assert doc.embfile_count() == 0
    raw = pdf.read_bytes()
    assert b"/JavaScript" not in raw and b"/JS" not in raw and b"/EmbeddedFile" not in raw
    pages = _render_and_scan(tmp_path, pdf)
    assert pages[0] == []                                               # 封面沒有 QR
    got = [t for p in pages[1:] for t in p]
    assert sorted(got) == sorted(made["main"]), (len(got), len(made["main"]))
    # 掃回來的主文件＋任兩份分片：獨立實作解得開
    env = independent_restore(got, made["shares"][:2])
    assert env["content"] == content


def test_each_share_pdf_has_its_qr_and_matching_code(tmp_path):
    import fitz
    made = _pdf_set(tmp_path, kind=1, name="n", content=b"secret", t=2, n=3, density="medium", paper="letter")
    for i in range(3):
        pdf = tmp_path / f"share-{i + 1}.pdf"
        with fitz.open(pdf) as doc:
            assert doc.page_count == 1
            w, h = doc[0].rect.width, doc[0].rect.height
            assert abs(w - 612) < 1 and abs(h - 792) < 1                # Letter
        texts = _render_and_scan(tmp_path, pdf)
        assert texts == [[made["shares"][i]]]


def test_zip_of_all_files_opens_with_a_standard_unzipper(tmp_path):
    import zipfile
    out = tmp_path / "all.zip"
    script = (
        "import { makeZip } from './static/js/paper_backup/pb_zip.mjs';"
        "import { writeFileSync } from 'node:fs';"
        "const enc = new TextEncoder();"
        "const files = [{ name: 'JTPB-AAAA-main.pdf', data: enc.encode('%PDF main') },"
        "               { name: '分片-1.pdf', data: new Uint8Array(70000).map((_, i) => i % 251) }];"
        f"writeFileSync({json.dumps(str(out))}, makeZip(files, new Date(2026, 10, 9, 12, 30, 0)));"
    )
    r = subprocess.run([NODE, "--input-type=module", "-e", script], cwd=ROOT, capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stderr
    with zipfile.ZipFile(out) as z:
        assert z.testzip() is None
        assert z.namelist() == ["JTPB-AAAA-main.pdf", "分片-1.pdf"]
        assert z.read("JTPB-AAAA-main.pdf") == b"%PDF main"
        assert z.read("分片-1.pdf") == bytes(i % 251 for i in range(70000))
        assert z.getinfo("分片-1.pdf").date_time == (2026, 11, 9, 12, 30, 0)


@pytest.mark.parametrize("which", ["main", "share"])
def test_watermark_stays_in_the_margins_and_never_touches_a_qr(tmp_path, which):
    """浮水印（產生資訊）只在頁尾與左邊界，跟任何 QR 與它的靜區都不重疊（使用者 2026-11-09：不要影響識別）。"""
    import fitz
    made = _pdf_set(tmp_path, kind=2, name="x", content=os.urandom(2600), t=2, n=2, density="high",
                    watermark="user@ldap · IP 10.0.0.1 · host · 2026/11/09 23:59")
    pdf = tmp_path / ("main.pdf" if which == "main" else "share-1.pdf")
    mm = 72 / 25.4
    with fitz.open(pdf) as doc:
        for page in doc:
            qr = [d["rect"] for d in page.get_drawings() if d.get("fill") == (0.0, 0.0, 0.0) and len(d["items"]) > 40]
            imgs = [fitz.Rect(i["bbox"]) for i in page.get_image_info()]
            foot = [r for r in imgs if r.y0 > page.rect.height - 15 * mm]
            side = [r for r in imgs if r.x1 < 15 * mm]
            assert foot and side, "每一頁都要有頁尾與左邊界的浮水印"
            for w in foot + side:
                for q in qr:
                    quiet = fitz.Rect(q.x0 - 4 * 1.0 * mm, q.y0 - 4 * 1.0 * mm, q.x1 + 4 * 1.0 * mm, q.y1 + 4 * 1.0 * mm)
                    assert not w.intersects(quiet), (w, q)
    texts = _render_and_scan(tmp_path, pdf)
    want = made["main"] if which == "main" else made["shares"][:1]
    assert sorted(t for p in texts for t in p) == sorted(want)


def test_no_weak_randomness_in_the_modules():
    """C05：金鑰、nonce、備份 ID、分片座標只能來自 crypto.getRandomValues；不可以有 Math.random / 時間當亂數。"""
    import re
    for f in sorted((ROOT / "static" / "js" / "paper_backup").glob("*.mjs")) + [ROOT / "static/vendor/shamir-secret-sharing-0.0.3/csprng.js"]:
        src = f.read_text(encoding="utf-8")
        assert "Math.random" not in src, f.name
        assert not re.search(r"(?:Date\.now|performance\.now)\(\)\s*[%^&|]", src), f.name
    csprng = (ROOT / "static/vendor/shamir-secret-sharing-0.0.3/csprng.js").read_text(encoding="utf-8")
    assert "crypto.getRandomValues" in csprng
    core = (ROOT / "static/js/paper_backup/pb_core.mjs").read_text(encoding="utf-8")
    assert "crypto.getRandomValues" in core


# ---------------- 掃描強度（P06 / P07）：轉向、歪斜、局部弄髒、整頁遺失 ----------------

def _page_images(pdf: Path, dpi: int = 200):
    import fitz
    from PIL import Image
    out = []
    with fitz.open(pdf) as doc:
        for page in doc:
            pix = page.get_pixmap(dpi=dpi, alpha=False)
            out.append(Image.frombytes("RGB", (pix.width, pix.height), pix.samples))
    return out


def _scan_images(tmp_path, imgs, tag) -> list[list[str]]:
    items = []
    for i, img in enumerate(imgs):
        rgba = img.convert("RGBA")
        f = tmp_path / f"{tag}-{i}.rgba"
        f.write_bytes(rgba.tobytes())
        items.append({"file": str(f), "w": rgba.width, "h": rgba.height})
    lst = tmp_path / f"{tag}.json"
    lst.write_text(json.dumps(items), encoding="utf-8")
    return json.loads(_node("tests/js/pb_cli.mjs", "scan", str(lst), timeout=600))


@pytest.mark.parametrize("how", ["rotate90", "rotate180", "skew3", "smudge", "blur"])
def test_scanning_survives_real_world_damage(tmp_path, how):
    """P06 / P07：掃反、拍歪、QR 局部被弄髒、拍得有點糊 —— 每一個區塊都要讀得回來（糾錯 Q 約可補 25%）。"""
    from PIL import Image, ImageDraw, ImageFilter
    made = _pdf_set(tmp_path, kind=2, name="x", content=os.urandom(2600), t=2, n=2, density="medium")
    page = _page_images(tmp_path / "main.pdf")[1]
    if how == "rotate90":
        page = page.rotate(90, expand=True, fillcolor="white")
    elif how == "rotate180":
        page = page.rotate(180, expand=True, fillcolor="white")
    elif how == "skew3":
        page = page.rotate(3, expand=True, fillcolor="white", resample=Image.BICUBIC)
    elif how == "smudge":
        d = ImageDraw.Draw(page)
        # 每個 QR 中間各蓋一塊約 8% 面積的污漬（避開三個定位角）
        w, h = page.size
        for cx in (0.25, 0.5, 0.75):
            for cy in (0.2, 0.42, 0.64, 0.86):
                x, y = int(w * cx), int(h * cy)
                d.ellipse([x - 40, y - 40, x + 40, y + 40], fill=(255, 255, 255))
    elif how == "blur":
        page = page.resize((page.width * 3 // 4, page.height * 3 // 4), Image.BILINEAR).filter(ImageFilter.GaussianBlur(0.8))
    got = [t for p in _scan_images(tmp_path, [page], how) for t in p]
    assert sorted(got) == sorted(made["main"]), (how, len(got), len(made["main"]))


def test_a_lost_page_is_reported_not_guessed(tmp_path):
    """P07 / F10：整頁遺失 → 缺的區塊列出來，足量分片也不能還原（不猜、不補）。"""
    made = _pdf_set(tmp_path, kind=2, name="x", content=os.urandom(4000), t=2, n=2, density="medium")
    pages = _page_images(tmp_path / "main.pdf")
    assert len(pages) >= 3                                              # 封面＋至少兩頁區塊
    got = [t for p in _scan_images(tmp_path, pages[2:3], "lost") for t in p]  # 只給第二頁區塊
    f = tmp_path / "in.json"
    f.write_text(json.dumps({"main": got, "shares": made["shares"]}), encoding="utf-8")
    out = json.loads(_node("tests/js/pb_cli.mjs", "restore", str(f)))
    assert out == {"ok": False, "code": "NOT_READY"}


@pytest.mark.parametrize("paper", ["a4", "letter"])
def test_print_test_page_fits_one_page_scans_back_and_has_a_true_50mm_ruler(tmp_path, paper):
    """P03：列印測試頁兩頁 —— 第 1 頁三種密度共 9 個 QR 全部掃得回來且內容一致、尺規剛好 50 mm；
    第 2 頁的公開示範備份（主文件＋2 份分片）掃回來真的還原得出預期的文字（還原驗證）。"""
    import fitz
    made = json.loads(_node("tests/js/pb_cli.mjs", "testpdf", json.dumps({"out": str(tmp_path), "paper": paper})))
    pdf = tmp_path / "test.pdf"
    mm = 72 / 25.4
    with fitz.open(pdf) as doc:
        assert doc.page_count == 2
        page = doc[0]
        # 尺規主線：一條寬 50 mm、高 0.3 mm 的矩形
        rects = [it[1] for d in page.get_drawings() for it in d["items"] if it[0] == "re"]
        assert any(abs(r.width - 50 * mm) < 0.05 and abs(r.height - 0.3 * mm) < 0.05 for r in rects), "找不到 50 mm 的尺規"
        assert all(r.y1 <= page.rect.height - 15 * mm + 0.5 for r in rects if r.width > 3 * mm), "內容超出下邊界"
    pages = _render_and_scan(tmp_path, pdf)
    assert sorted(pages[0]) == sorted(made["expected"])
    assert sorted(pages[1]) == sorted(made["demo"])
    # 還原驗證：只用第 2 頁掃回來的 QR，真的解一次
    main = [t for t in pages[1] if t in made["demo"][:1]]
    shares = [t for t in pages[1] if t in made["demo"][1:]]
    env = independent_restore(main, shares)
    assert env["content"].decode() == made["demoContent"]
