from local_scrape.parsing import parse_html


def test_html_removes_executable_text_and_resolves_links():
    out = parse_html('<title> Shop </title><meta name="description" content="Goods"><h1>Kettle</h1><script>secret()</script><a href="/buy">Buy</a><img src="/k.png" alt="K">', 'https://example.com/item')
    assert out['title'] == 'Shop'
    assert 'secret' not in out['text']
    assert out['links'] == [{'url':'https://example.com/buy', 'text':'Buy'}]
    assert out['images'] == [{'url':'https://example.com/k.png', 'alt':'K'}]
    assert out['metadata']['description'] == 'Goods'
    assert '# Kettle' in out['markdown']


def test_pdf_extracts_real_text_and_reports_page_budget():
    import io
    from pypdf import PdfWriter
    from pypdf.generic import DictionaryObject,NameObject,DecodedStreamObject
    from local_scrape.parsing import parse_pdf
    writer = PdfWriter()
    for _ in range(2):
        page = writer.add_blank_page(width=300,height=300)
        font = DictionaryObject({NameObject('/Type'):NameObject('/Font'),NameObject('/Subtype'):NameObject('/Type1'),NameObject('/BaseFont'):NameObject('/Helvetica')})
        page[NameObject('/Resources')] = DictionaryObject({NameObject('/Font'):DictionaryObject({NameObject('/F1'):font})})
        stream = DecodedStreamObject()
        stream.set_data(b'BT /F1 12 Tf 20 250 Td (Copper Kettle) Tj ET')
        page[NameObject('/Contents')] = stream
    output = io.BytesIO()
    writer.write(output)
    parsed = parse_pdf(output.getvalue(),max_pages=1)
    assert parsed['text'] == 'Copper Kettle'
    assert parsed['truncated'] and parsed['metadata']['pages'] == 2
    assert parsed['metadata']['pages_processed'] == 1
