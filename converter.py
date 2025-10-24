import re
import base64
from pathlib import Path
from typing import Optional, Union

# ==== dependências opcionais ====
try:
    import pikepdf  # pip install pikepdf
    HAS_PIKEPDF = True
except Exception:
    HAS_PIKEPDF = False

try:
    from PIL import Image  # pip install pillow
    HAS_PIL = True
except Exception:
    HAS_PIL = False

HEX_CHARS = set("0123456789abcdefABCDEF")

# ---------- leitura tolerante ----------
def _read_text_any(path: Path) -> str:
    raw = path.read_bytes()
    for enc in ("utf-8", "utf-16-le", "utf-16-be", "latin-1"):
        try:
            return raw.decode(enc)
        except Exception:
            pass
    return raw.decode("latin-1", errors="ignore")

def _clean_hex(s: str) -> str:
    s = s.strip()
    if s.startswith(("0x","0X","\\x")):
        s = s[2:]
    return "".join(ch for ch in s if ch in HEX_CHARS)

def _looks_like_b64(s: str) -> bool:
    s = re.sub(r"\s+","",s)
    return bool(re.fullmatch(r"[A-Za-z0-9+/]+={0,2}", s)) and len(s) >= 8

def _dec_bytes_from_text(s: str) -> Optional[bytes]:
    toks = re.findall(r"\d+", s)
    if not toks:
        return None
    vals=[]
    for t in toks:
        v = int(t)
        if not (0 <= v <= 255):
            return None
        vals.append(v)
    return bytes(vals)

def _to_bytes(raw_text: str) -> bytes:
    s = raw_text.strip()

    # data URL
    if s.lower().startswith("data:") and ";base64," in s:
        b64 = s.split(";base64,",1)[1]
        return base64.b64decode(re.sub(r"\s+","", b64))

    # base64 puro
    compact = re.sub(r"\s+","", s)
    if _looks_like_b64(compact):
        try:
            return base64.b64decode(compact)
        except Exception:
            pass

    # decimal textual
    dec = _dec_bytes_from_text(s)
    if dec is not None:
        return dec

    # hex “sujo”
    cleaned = _clean_hex(s)
    if cleaned:
        if len(cleaned) % 2 != 0:
            cleaned = cleaned[:-1]
        return bytes.fromhex(cleaned)

    raise ValueError("Conteúdo não parece Base64, decimal ou HEX.")

# ---------- carving de PDFs ----------
PDF_START = b"%PDF-"
PDF_EOF   = b"%%EOF"

def carve_pdf_segments(b: bytes) -> list[bytes]:
    """Extrai todos os blocos %PDF- ... %%EOF do buffer."""
    parts = []
    i = 0
    while True:
        a = b.find(PDF_START, i)
        if a == -1: break
        z = b.find(PDF_EOF, a)
        if z == -1:
            # não achou EOF — pega até o fim (pode estar truncado)
            parts.append(b[a:])
            break
        z_end = z + len(PDF_EOF)
        # engole CR/LF
        while z_end < len(b) and b[z_end:z_end+1] in (b"\r", b"\n"):
            z_end += 1
        parts.append(b[a:z_end])
        i = z_end
    return parts

# ---------- data recovery de imagens ----------
def _find_jpeg_spans(b: bytes):
    i = 0
    while True:
        a = b.find(b"\xFF\xD8", i)
        if a == -1: break
        z = b.find(b"\xFF\xD9", a+2)
        if z == -1: break
        yield (a, z+2)
        i = z+2

def _find_png_spans(b: bytes):
    sig = b"\x89PNG\r\n\x1a\n"
    i = 0
    while True:
        a = b.find(sig, i)
        if a == -1: break
        z = b.find(b"IEND\xAE\x42\x60\x82", a+8)
        if z == -1: break
        yield (a, z+8)
        i = z+8

def recover_images_to_pdf(b: bytes, out_pdf: Path) -> Optional[Path]:
    if not HAS_PIL:
        return None
    from io import BytesIO
    imgs = []
    for a,z in _find_jpeg_spans(b):
        imgs.append(b[a:z])
    for a,z in _find_png_spans(b):
        imgs.append(b[a:z])
    if not imgs:
        return None
    pages=[]
    for data in imgs:
        try:
            with Image.open(BytesIO(data)) as im:
                if im.mode in ("RGBA","P"):
                    im = im.convert("RGB")
                pages.append(im.copy())
        except Exception:
            pass
    if not pages:
        return None
    first, rest = pages[0], pages[1:]
    first.save(out_pdf, "PDF", save_all=True, append_images=rest)
    return out_pdf

# ---------- pipeline principal ----------
def processar_txt(diretorio: Union[str, Path], nome_txt: str):
    d = Path(diretorio)
    t = d / nome_txt
    if not t.exists():
        raise FileNotFoundError(t)

    raw_text = _read_text_any(t)
    blob = _to_bytes(raw_text)

    parts = carve_pdf_segments(blob)
    if not parts:
        print("❌ Não encontrei nenhum bloco %PDF- ... %%EOF no TXT.")
        # ainda assim, tentar recuperar imagens do blob inteiro
        rec = recover_images_to_pdf(blob, d / "recuperado_do_blob.pdf")
        if rec:
            print(f"🖼️  PDF de imagens recuperado: {rec}")
        return

    print(f"🧩 Encontrei {len(parts)} bloco(s) PDF no TXT.")
    for idx, part in enumerate(parts, start=1):
        base = d / f"pdf_part_{idx:02d}.pdf"
        base.write_bytes(part)
        print(f"  • Salvei {base.name}  (len={len(part)})")

        # tentar reparar com pikepdf
        if HAS_PIKEPDF:
            try:
                repaired = d / f"pdf_part_{idx:02d}_reparado.pdf"
                with pikepdf.open(base) as pdf:
                    pdf.save(repaired)
                print(f"    ✅ Reparado com pikepdf: {repaired.name}")
                continue
            except Exception as e:
                print(f"    ⚠️  pikepdf falhou: {e}")

        # tentar recuperar imagens desta parte
        rec = recover_images_to_pdf(part, d / f"pdf_part_{idx:02d}_recuperado.pdf")
        if rec:
            print(f"    🖼️  Criado {rec.name} com imagens extraídas.")
# ===== Execução direta =====
if __name__ == "__main__":
    d = input("Diretório do TXT: ").strip()
    f = input("Nome do TXT (ex: informe.txt): ").strip()
    info = reparar_ou_recuperar_pdf_de_txt(d, f)
    print(info)
