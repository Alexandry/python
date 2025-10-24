import re
import base64
from pathlib import Path
from typing import Optional, Union

# ----------------- Leitura/decodificação tolerante -----------------
def _read_text_any(path: Path) -> str:
    raw = path.read_bytes()
    for enc in ("utf-8", "utf-16-le", "utf-16-be", "latin-1"):
        try:
            return raw.decode(enc)
        except Exception:
            pass
    return raw.decode("latin-1", errors="ignore")

def _looks_like_b64(s: str) -> bool:
    s = re.sub(r"\s+", "", s)
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

def _clean_hex(s: str) -> str:
    s = s.strip()
    if s.startswith(("0x","0X","\\x")):
        s = s[2:]
    return "".join(ch for ch in s if ch in HEX_CHARS)

def decode_txt_to_bytes(raw_text: str) -> bytes:
    s = raw_text.strip()

    # data:...;base64,...
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

# ----------------- Garimpo de imagens (PNG/JPEG) -----------------
def find_jpeg_spans(b: bytes):
    i = 0
    while True:
        a = b.find(b"\xFF\xD8", i)  # SOI
        if a == -1: break
        z = b.find(b"\xFF\xD9", a+2)  # EOI
        if z == -1: break
        yield (a, z+2)
        i = z+2

def find_png_spans(b: bytes):
    sig = b"\x89PNG\r\n\x1a\n"
    i = 0
    while True:
        a = b.find(sig, i)
        if a == -1: break
        z = b.find(b"IEND\xAE\x42\x60\x82", a+8)
        if z == -1: break
        yield (a, z+8)
        i = z+8

def extract_images(b: bytes):
    """Retorna lista de tuplas (fmt, bytes) em ordem de aparição."""
    imgs = []
    for a,z in find_jpeg_spans(b):
        imgs.append(("jpg", b[a:z]))
    for a,z in find_png_spans(b):
        imgs.append(("png", b[a:z]))
    # Ordena por posição original (misturando png/jpg)
    # Para isso, revarremos mapeando posições:
    pos_map = []
    for a,z in find_jpeg_spans(b):
        pos_map.append((a, ("jpg", b[a:z])))
    for a,z in find_png_spans(b):
        pos_map.append((a, ("png", b[a:z])))
    pos_map.sort(key=lambda x: x[0])
    return [item for _, item in pos_map]

# ----------------- Reempacotar em PDF -----------------
def images_to_pdf(images: list[tuple[str, bytes]], out_pdf: Path) -> Path:
    pages = []
    for kind, data in images:
        with Image.open(BytesIO(data)) as im:
            if im.mode in ("RGBA", "P"):
                im = im.convert("RGB")
            pages.append(im.copy())
    if not pages:
        raise RuntimeError("Nenhuma imagem válida para montar o PDF.")
    first, rest = pages[0], pages[1:]
    first.save(out_pdf, "PDF", save_all=True, append_images=rest)
    return out_pdf

# ----------------- Pipeline principal -----------------
def montar_pdf_de_imagens_do_txt(diretorio: Union[str, Path], nome_txt: str,
                                 pdf_saida: str = "informe_por_imagem.pdf") -> Path:
    d = Path(diretorio)
    t = d / nome_txt
    if not t.exists():
        raise FileNotFoundError(t)

    raw_text = _read_text_any(t)
    blob = decode_txt_to_bytes(raw_text)

    # 1) tenta achar imagens no blob inteiro (independente de ter “%PDF-”)
    imgs = extract_images(blob)
    print(f"Imagens encontradas: {len(imgs)}")
    if not imgs:
        raise RuntimeError("Nenhuma imagem PNG/JPEG foi encontrada no conteúdo.")

    # 2) salva também os arquivos de imagem individuais (útil para conferir)
    for idx, (kind, data) in enumerate(imgs, start=1):
        (d / f"imagem_{idx:02d}.{kind}").write_bytes(data)

    # 3) monta um PDF com 1 imagem por página
    out_pdf = d / pdf_saida
    images_to_pdf(imgs, out_pdf)
    print(f"✅ PDF gerado a partir das imagens: {out_pdf.resolve()}")
    return out_pdf
if __name__ == "__main__":
    d = input("Diretório do TXT: ").strip()
    f = input("Nome do TXT (ex: informe.txt): ").strip()
    info = reparar_ou_recuperar_pdf_de_txt(d, f)
    print(info)
