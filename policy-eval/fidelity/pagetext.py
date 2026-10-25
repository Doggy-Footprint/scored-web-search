#!/usr/bin/env python3
"""pagetext.py URL [OUT] -- curl a page and print/save its visible text (no model involved)."""
import sys, re, subprocess, html
url = sys.argv[1]
raw = subprocess.run(['curl', '-sL', '--max-time', '25', '-A', 'Mozilla/5.0', url], capture_output=True).stdout
if raw[:4] == b'%PDF':
    t = '[PDF: not extracted]'
else:
    h = raw.decode('utf-8', 'ignore')
    h = re.sub(r'(?is)<(script|style|noscript|svg|nav|footer|header)[^>]*>.*?</\1>', ' ', h)
    h = re.sub(r'(?s)<!--.*?-->', ' ', h)
    h = re.sub(r'(?i)<br\s*/?>|</p>|</li>|</h\d>|</tr>|</td>', '\n', h)
    h = html.unescape(re.sub(r'<[^>]+>', ' ', h))
    t = re.sub(r'[ \t]+', ' ', re.sub(r'\n\s*\n+', '\n', h)).strip()
if len(sys.argv) > 2:
    open(sys.argv[2], 'w').write(f'URL: {url}\n\n' + t)
    print(f'{len(t)} chars -> {sys.argv[2]}')
else:
    print(t)
