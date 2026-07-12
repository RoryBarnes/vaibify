---
name: read-arxiv
description: Read a scientific paper token-efficiently by fetching its arXiv TeX source instead of the PDF. Use whenever asked to read, summarize, check, or consult a paper, preprint, or arXiv posting.
---

# Reading papers: TeX source first, PDF last

The arXiv e-print source of a paper is typically 10-50x fewer tokens
than the extracted PDF text, figure captions arrive as searchable
text (often with qualitative descriptions of what each figure shows),
and equations arrive as TeX instead of mangled glyphs. Always try the
source first.

## Procedure

1. **Resolve the arXiv id.** If given a title or citation instead of
   an id/URL, query the arXiv API (an Atom feed, no key needed):
   `curl -s "http://export.arxiv.org/api/query?search_query=all:<urlencoded terms>&max_results=3"`
   and confirm the match by title and authors before proceeding.
2. **Fetch the e-print source** into a temp directory:
   `mkdir -p /tmp/arxiv-<id> && curl -sL https://arxiv.org/e-print/<id> -o /tmp/arxiv-<id>/src`
   The payload is a gzipped tar, a bare gzipped .tex, or (rarely) a
   PDF. Check with `file src`, then `tar -xf` or `gunzip` as needed.
3. **Find the main file**: `grep -l '\\documentclass' *.tex`. Follow
   `\input{}` / `\include{}` references as needed.
4. **Read selectively, not linearly.** Abstract first, then only the
   sections relevant to the question, then figure captions
   (`grep -n 'caption' *.tex` locates them all). The `.bbl` file
   holds the resolved reference list. Do not read the whole source
   into context when a targeted read answers the question.
5. **Record which version you read.** The e-print URL serves the
   latest version; the API response lists the version number. State
   it (e.g. "arXiv:2401.00001v2") in your summary — papers change
   between versions, and a claim sourced to the wrong version is a
   citation error.
6. **Fall back to the PDF only when there is no usable source**
   (author submitted PDF-only, or the source fails to extract):
   `https://arxiv.org/pdf/<id>`. Say in your summary that you read
   the PDF and why.
7. Clean up `/tmp/arxiv-<id>` when done.

## Caveats

- Respect network isolation: if the container has no egress, report
  that instead of hanging on curl (use `--max-time 30`).
- Figures themselves are images in the source tarball; the captions
  and the prose ARE the token-efficient description. Only view a
  figure file directly when the question truly requires it.
- For non-arXiv papers (journal-only), this skill does not apply;
  say so rather than guessing at paywalled content.
