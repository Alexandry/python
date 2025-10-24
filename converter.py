import jaydebeapi  # pip install JayDeBeApi
import jpype

# Opcional (fallback imagem -> PDF)
try:
    from PIL import Image
    from io import BytesIO
    HAS_PIL = True
except Exception:
    HAS_PIL = False

HEX_CHARS = set("0123456789abcdefABCDEF")

def _clean_hex(s: str) -> str:
    s = s.strip()
    if s.startswith(("0x","0X","\\x")):
        s = s[2:]
    return "".join(ch for ch in s if ch in HEX_CHARS)

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

def to_bytes_best_effort(value: Union[bytes, bytearray, str]) -> bytes:
    """Converte retorno da coluna para bytes: bytes diretos, hex textual, base64 ou decimal."""
    if isinstance(value, (bytes, bytearray)):
        return bytes(value)
    if not isinstance(value, str):
        raise TypeError(f"Tipo inesperado: {type(value)}")

    s = value.strip()

    # data:...;base64,...
    if s.lower().startswith("data:") and ";base64," in s:
        b64 = s.split(";base64,",1)[1]
        return base64.b64decode(re.sub(r"\s+","", b64))

    # base64 puro?
    compact = re.sub(r"\s+","", s)
    if _looks_like_b64(compact):
        try:
            return base64.b64decode(compact)
        except Exception:
            pass

    # decimal textual?
    dec = _dec_bytes_from_text(s)
    if dec is not None:
        return dec

    # hex textual “sujo”
    cleaned = _clean_hex(s)
    if cleaned:
        if len(cleaned) % 2 != 0:
            cleaned = cleaned[:-1]
        return bytes.fromhex(cleaned)

    raise ValueError("Valor textual não parece Base64, decimal ou HEX.")

def is_pdf(b: bytes) -> bool:
    return b.startswith(b"%PDF-")

def trim_to_eof(pdf_bytes: bytes) -> bytes:
    idx = pdf_bytes.rfind(b"%%EOF")
    if idx == -1:
        return pdf_bytes
    end = idx + len(b"%%EOF")
    while end < len(pdf_bytes) and pdf_bytes[end:end+1] in (b"\r", b"\n"):
        end += 1
    return pdf_bytes[:end]

def find_first_image_span(b: bytes):
    # JPEG
    a = b.find(b"\xFF\xD8")
    if a != -1:
        z = b.find(b"\xFF\xD9", a+2)
        if z != -1:
            return ("jpg", a, z+2)
    # PNG
    sig = b"\x89PNG\r\n\x1a\n"
    a = b.find(sig)
    if a != -1:
        z = b.find(b"IEND\xAE\x42\x60\x82", a+8)
        if z != -1:
            return ("png", a, z+8)
    return None

def image_bytes_to_single_page_pdf(img_bytes: bytes, out_pdf: Path) -> Path:
    if not HAS_PIL:
        raise RuntimeError("Pillow não instalado; não é possível montar PDF da imagem.")
    with Image.open(BytesIO(img_bytes)) as im:
        if im.mode in ("RGBA","P"):
            im = im.convert("RGB")
        im.copy().save(out_pdf, "PDF")
    return out_pdf

def fetch_blob_via_jdbc_jtds(
    user: str,
    password: str,
    table: str,
    column: str,
    where_clause: str = "id = ?",
    param: Optional[Union[int, str]] = None,
    jtds_jar_path: str = "C:/drivers/jtds-1.3.1.jar",  # ajuste o caminho do seu .jar
):
    """
    Conecta no Sybase ASE via JDBC (jTDS) e retorna o valor da coluna.
    Host: sybbco.alenet.net:4400, DB: DBinforme
    """
    # Classe e URL JDBC (jTDS)
    driver_class = "net.sourceforge.jtds.jdbc.Driver"
    jdbc_url = (
        "jdbc:jtds:sybase://sybbco.alenet.net:4400/DBinforme;"
        "TDS=5.0;lastupdatecount=true;useCursors=true;CHARSET=utf8"
    )

    # Inicia a JVM se necessário
    if not jpype.isJVMStarted():
        jpype.startJVM(classpath=[jtds_jar_path])

    conn = jaydebeapi.connect(driver_class, jdbc_url, [user, password])
    try:
        cur = conn.cursor()
        # IMPORTANTÍSSIMO no Sybase ASE: evita truncamento de TEXT/IMAGE (~32KB)
        cur.execute("SET TEXTSIZE 2147483647")

        query = f"SELECT {column} FROM {table} WHERE {where_clause}"
        if param is None:
            cur.execute(query)
        else:
            cur.execute(query, (param,))
        row = cur.fetchone()
        if not row:
            raise RuntimeError("Nenhuma linha retornada.")
        return row[0]
    finally:
        conn.close()

def salvar_informe_sem_odbc(
    user: str,
    password: str,
    table: str,
    column: str,
    where_clause: str,
    param: Union[int, str],
    out_pdf_path: str = "informe.pdf",
    jtds_jar_path: str = "C:/drivers/jtds-1.3.1.jar",
) -> Path:
    valor = fetch_blob_via_jdbc_jtds(
        user=user, password=password,
        table=table, column=column,
        where_clause=where_clause, param=param,
        jtds_jar_path=jtds_jar_path
    )
    blob = to_bytes_best_effort(valor)
    print("Tamanho recebido (bytes):", len(blob))
    print("Cabeçalho (8 bytes):", blob[:8].hex().upper())

    out_path = Path(out_pdf_path)

    if is_pdf(blob):
        trimmed = trim_to_eof(blob)
        out_path.write_bytes(trimmed)
        print(f"PDF salvo em: {out_path.resolve()}  (len={len(trimmed)})")
        return out_path

    # Se não for PDF, tenta imagem única → PDF
    span = find_first_image_span(blob)
    if span:
        kind, a, z = span
        img_bytes = blob[a:z]
        img_path = out_path.with_suffix(f".{kind}")
        img_path.write_bytes(img_bytes)
        print(f"Imagem extraída: {img_path.name}  (len={len(img_bytes)})")
        image_bytes_to_single_page_pdf(img_bytes, out_path)
        print(f"PDF (a partir da imagem) salvo em: {out_path.resolve()}")
        return out_path

    # Se não reconheceu, salva dump p/ análise
    dump = out_path.with_suffix(".bin")
    dump.write_bytes(blob)
    raise RuntimeError(
        "Conteúdo não é PDF e nenhuma imagem JPEG/PNG foi detectada. "
        f"Dump salvo: {dump}"
    )

# ===== Execução direta (exemplo) =====
if __name__ == "__main__":
    usuario = input("Usuário do DB: ").strip()
    senha   = input("Senha do DB: ").strip()

    # Ajuste o caminho do .jar do jTDS abaixo:
    jtds_jar = r"C:\drivers\jtds-1.3.1.jar"  # ou "/opt/drivers/jtds-1.3.1.jar"

    # Informe sua tabela/coluna/where:
    tabela = "dbo.SuaTabela"
    coluna = "arquivo"        # IMAGE/TEXT/VARBINARY/VARCHAR com HEX/Base64
    where  = "id = ?"
    param  = 123

    try:
        salvar_informe_sem_odbc(
            user=usuario, password=senha,
            table=tabela, column=coluna,
            where_clause=where, param=param,
            out_pdf_path="informe.pdf",
            jtds_jar_path=jtds_jar
        )
    except Exception as e:
        print("Erro:", e)
