"""Bounded document normalization; scripts never become extraction instructions."""
import io
import re
from urllib.parse import urljoin, urlsplit, urldefrag
from bs4 import BeautifulSoup
from markdownify import markdownify
from .security import ScrapeError


def normalize_text(text):
    return '\n'.join(line for line in (re.sub(r'\s+', ' ', row).strip() for row in text.splitlines()) if line)


def parse_html(html, url=''):
    soup = BeautifulSoup(html, 'html.parser')
    title = soup.title.get_text(' ', strip=True) if soup.title else ''
    metadata = {}
    for meta in soup.select('meta[name], meta[property]'):
        name = meta.get('name') or meta.get('property')
        if meta.get('content') is not None:
            metadata[name] = meta['content']
    for item in soup.select('script, style, noscript, template, svg'):
        item.decompose()
    links, images, seen = [], [], set()
    def absolute(value):
        target = urldefrag(urljoin(url, value))[0]
        return target if urlsplit(target).scheme in ('http', 'https') else None
    for link in soup.select('a[href]'):
        target = absolute(link['href'])
        if target and target not in seen:
            seen.add(target)
            links.append({'url':target, 'text':link.get_text(' ', strip=True)})
            link['href'] = target
    for image in soup.select('img[src]'):
        target = absolute(image['src'])
        if target:
            images.append({'url':target, 'alt':image.get('alt', '')})
            image['src'] = target
    body = soup.body or soup
    return dict(title=title, text=normalize_text(body.get_text('\n', strip=True)),
                markdown=markdownify(str(body), heading_style='ATX').strip(),
                links=links, images=images, metadata=metadata)


def parse_pdf(body, max_chars=100000, max_pages=100):
    from pypdf import PdfReader
    try:
        reader = PdfReader(io.BytesIO(body))
        if reader.is_encrypted:
            raise ScrapeError('PDF_ENCRYPTED', 'Encrypted PDFs are unsupported', 422)
        texts, size, processed = [], 0, 0
        total = len(reader.pages)
        for page in reader.pages[:max_pages]:
            value = page.extract_text() or ''
            texts.append(value[:max(0, max_chars-size)])
            size += len(value)
            processed += 1
            if size >= max_chars:
                break
        partial = processed < total or size > max_chars
        text = normalize_text('\n'.join(texts))
        return dict(title=str((reader.metadata or {}).get('/Title', '')), text=text,
                    markdown=text, links=[], images=[], metadata={'pages':total, 'pages_processed':processed, 'partial':partial},
                    truncated=partial, warnings=['pdf_partial'] if partial else [])
    except ScrapeError:
        raise
    except Exception:
        raise ScrapeError('PDF_ERROR', 'PDF could not be decoded', 422) from None
