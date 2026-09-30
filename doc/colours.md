# Colours

Mobilito is a project of Les Mobilitains, so it uses the association's
colours and logo. This page says which colours we use, what each one
is for, which combinations are allowed, and how that's encoded in the
code. The values live in one place,
[`core/static/core/css/mobilitains.css`](../core/static/core/css/mobilitains.css);
everything else refers to them.

## Rule zero: accessibility first

Where the brand documents and accessibility disagree, **accessibility
wins**. Every text/background pair on the site must meet WCAG 2.2 AA:

- **4.5:1** for normal text;
- **3:1** for large text (24px, or 18.7px bold), icons, form-field
  edges, focus rings and other parts of controls;
- colour is never the only signal: errors have words, map pins have
  a legend, chart bars have labels and a table.

Several combinations the brand guide shows (white on the emblem blue,
for instance) fail this, and aren't used. Our users are volunteers
reading phones outdoors, often in sunlight, so we treat these numbers
as a floor, not a target.

## Sources

| Source | Date | What it gives |
|---|---|---|
| *Guide visuel des Mobilitains* (`communication/charte-graphique/20211221-guide-visuel-mobilitains.pdf`) | Dec. 2021, latest | The five-colour palette with hex/RGB/CMYK/Pantone, permitted and forbidden two-colour pairs, the logo and its variants, the two call-to-action buttons, Montserrat. |
| *Charte couleur* (`communication/charte-couleur.pdf`) | earlier | The same palette, plus what the colours *mean*: blue calls to action, orange is for membership and donations, rouge-gris is the main colour and the heart of the logo. |
| The colour grid `artwork/M-couleurs.png` | | Each colour as text on each other colour; a black dot marks the four pairs never to use. |
| The Mobilitains proof of concept (`transport-nantes/tn_web`, `asso_tn/static/asso_tn/mobilitains.css` and `mobilito/…/base_mobilito.html`) | 2021–22 | How it was applied: navy background, the white logo with its coloured dots centred in the header, dark blue body text, rouge-gris for muted text. |

Where they disagree, we follow the guide, which is the latest and was
made by the designers, unless all the other sources agree against it.
Each difference is noted below.

## The palette

| Name (brand) | Token | Hex | RGB | Pantone | Used for |
|---|---|---|---|---|---|
| Bleu emblème | `--mbl-bleu` | `#5BC2E7` | 91 194 231 | 2985 C&U | Contributing: count, report, send (the call to action). Chart and map category 1. |
| Bleu +1 (marine) | `--mbl-marine` | `#43526E` | 67 82 110 | 432 C&U | The header, headings, links, navigation buttons. |
| Gris | `--mbl-gris` | `#DBE3EB` | 219 227 235 | 656 C&U | Surfaces, pressed states. |
| Rouge mobilitains (orange) | `--mbl-orange` | `#FA4616` | 250 70 22 | 172 C&U | Accents: the logo's dots, report pins, the map crosshair. Its darker step is used for errors and warnings. |
| Marron / rouge-gris | `--mbl-marron` | `#7F6966` | 127 105 102 | 4715 C&U | The heart of the logo; secondary and help text. |
| Bleu foncé | `--mbl-fonce` | `#333F48` | 51 63 72 | | Body text. From the guide's secondary palette and the proof of concept. |
| Bleu-gris | `--mbl-bleu-gris` | `#78909C` | 120 144 156 | | Form-field edges and dividers (3.4:1 on white). From the guide's secondary palette. |

Differences between sources:

- **Gris.** The guide says `#DBE3EB`; the *charte couleur* and the grid
  image use `#DDE5ED`. We use the guide's value; nobody can see the
  difference.
- **Marron.** The guide gives `#806966` (RGB 128 105 102); the *charte
  couleur*, the grid and the proof of concept use `#7F6966` (127 105
  102). We use `#7F6966`, which three sources agree on.
- **Dark blue.** The *charte couleur* gives Pantone 432 as RGB 51 63 72
  (`#333F48`); the guide gives Pantone 432 as `#43526E` and lists
  `#333F48` separately. We use both: `#43526E` is *marine*, the brand's
  second blue; `#333F48` is *foncé*, for text.
- **The logo files** use slightly different values again (`#56C0E7`,
  `#FB4400`, `#806966`). The logo is artwork: we use it as supplied and
  don't correct it.

### Lighter and darker steps

The brand documents give each colour once. A usable scheme also needs
a step lighter (hover, tints) and a step darker (pressed states, and
text that must read on white). Each step keeps the colour's hue and
moves its lightness by 0.08 in OKLCH, a colour space where equal steps
look equal.

| Colour | Light | Base | Dark | Notes |
|---|---|---|---|---|
| bleu | `#96D8F2` | `#5BC2E7` | `#3EA8CD` | Text step `--mbl-bleu-text` `#107998` (5.0:1 on white) for when the blue itself must be read or be an edge. Tint `--mbl-bleu-tint` `#D3F1FD` for information boxes. |
| marine | `#596986` | `#43526E` | `#2E3C57` | The dark step is the counting screen's background and the hover state of navy buttons. |
| gris | `#EAF2FA` | `#DBE3EB` | `#C4CCD4` | The light step is for large pale areas (chart tracks, subtle backgrounds). |
| orange | `#FD7E61` | `#FA4616` | `#D23404` | The dark step (5.0:1 on white, and white on it) carries text: errors, danger buttons, report icons. `--mbl-orange-darker` `#B32C03` for its hover. Tint `--mbl-orange-tint` `#FFE5DE`. |
| marron | `#97807D` | `#7F6966` | `#685350` | |
| foncé | | `#333F48` | `#1F2B33` | The dark step is text on the emblem blue (7.1:1). |

## Which colour goes with which

Contrast of text (row) on background (column's colour), with what the
brand says and what we allow on the web:

| Text | Background | Contrast | Brand | Web |
|---|---|---|---|---|
| gris | marine | 6.1 | allowed | text |
| marine | gris | 6.1 | allowed | text |
| gris | marron | 3.9 | allowed | large text and icons only |
| marron | gris | 3.9 | allowed | large text and icons only |
| bleu | marine | 3.9 | allowed | large text and icons only |
| marine | bleu | 3.9 | allowed | large text and icons only |
| marron | bleu | 2.5 | allowed | **no** |
| gris | orange | 2.7 | allowed | **no** |
| gris | bleu | 1.6 | allowed | **no** |
| bleu | gris | 1.6 | allowed | **no** |
| marron | marine | 1.5 | forbidden | no |
| marine | marron | 1.5 | forbidden | no |
| bleu | orange | 1.7 | forbidden | no |
| orange | bleu | 1.7 | forbidden | no |
| orange | gris, marron or marine | 1.4–2.7 | forbidden by the guide, allowed by the grid | no |
| marine | orange | 2.2 | forbidden by the guide, allowed by the grid | no |
| bleu | marron | 2.5 | forbidden by the guide, allowed by the grid | no |
| marron | orange | 1.4 | forbidden by the guide, allowed by the grid | no |

The guide and the grid image disagree only on pairs that fail
contrast anyway, so the disagreement doesn't matter on the web.

The pairs we actually use for text, beyond those above:

| Text | Background | Contrast | Where |
|---|---|---|---|
| foncé | white | 10.8 | body text |
| marine | white | 7.9 | headings, links, outline buttons |
| marron | white | 5.1 | help and secondary text |
| marron | gris light | 4.5 | secondary text on pale areas |
| white | marine | 7.9 | header, navigation buttons |
| white | marine dark | 11.1 | counting screen |
| foncé / foncé dark | bleu | 5.3 / 7.1 | contribute buttons, count buttons |
| white | orange dark | 5.0 | danger buttons ("Discard") |
| foncé dark | gris | 11.2 | "Finish" on the counting screen |
| orange dark | white | 5.0 | error messages, report icons |

**Never:** white or gris text on the emblem blue (2.0:1 / 1.6:1),
although the guide's call-to-action button shows it. **Never** orange
text on anything but white, and then only the dark step.

## What each part of the site uses

- **Page:** white background, foncé text, marine headings; secondary
  text (`.text-body-secondary`) in marron.
- **Header:** marine background, the logo in its version for dark
  backgrounds (white path and wordmark, coloured dots), as in the
  proof of concept. Phones show the logo's mark alone, then
  "Mobilito", with narrower buttons, so the language and sign-in
  buttons fit on one line at 360px even in French; below 360px the
  word "Mobilito" is hidden on screen (screen readers still read it).
- **Links:** marine, underlined; darker on hover.
- **Buttons.** The guide has two kinds of call to action, and so do
  we:
  - *Contribute* (`.btn-contribute`, `.btn-outline-contribute`): count,
    report, send, "make an observation". The emblem blue, with dark
    text. The *charte couleur* says blue "is a call to action; it
    prompts people to do something".
  - *Navigate* (`.btn-primary`, `.btn-outline-primary`): see more,
    back, sign in. Marine, white text.
  - *Danger* (`.btn-danger`, `.btn-outline-danger`): discard. Dark
    orange, white text.
- **Focus:** every button has a ring at least 3:1 against white, with
  a thin white gap between button and ring so it shows even on a
  button of the same colour:
  marine for navigation, links and light buttons, the blue's text
  step for contributing, dark orange for danger, dark blue for the
  yellow warning button. On the dark header and the counting
  screen's top bar the ring is white (but not in the counting
  screen's white pop-ups); so is the outline of the logo link and of
  the four count buttons. The close (X) button's ring is marine. A
  field with an error gets a dark orange ring, and Bootstrap's red
  error icon is redrawn in dark orange (text fields only: a
  `.form-select` would still get Bootstrap's red icon; none is used
  yet). Form fields have bleu-gris
  edges (3.4:1) and a marine ring.
- **Tinted boxes** (`.alert`) and pressed list rows: muted text
  switches to marron-dark and error text to orange-darker, because the
  usual steps fall just under 4.5:1 on the tints.
- **Status.** Errors use dark orange rather than adding a red that
  would clash with the brand's orange. Success and warning keep
  Bootstrap's green and yellow: they're status, not brand, and always
  come with words or an icon.
- **Counting screen:** marine-dark background; count buttons in the
  emblem blue with dark icons and numbers (white while being tapped);
  "Finish" in pale grey with dark text, deliberately *not* orange, so
  it can't be confused with "Discard".
- **Observation kinds:** counts are navy and reports orange, on the map
  (pins and legend) and as the icons beside them in lists
  (`.mbl-text-count`, `.mbl-text-report`). Groups of nearby
  observations are white circles with a dark ring and number, so they
  look like neither kind.
- **Page links:** the current page in navy.

### Charts and map categories

Charts need colours that stay distinct for colour-blind readers.
Checked with the dataviz palette validator, in this order:

| Slot | Token | Hex | Meaning today |
|---|---|---|---|
| 1 | `--mbl-chart-1` | `#5BC2E7` bleu | pedestrians |
| 2 | `--mbl-chart-2` | `#3D5C99` marine, saturated | bikes; count pins |
| 3 | `--mbl-chart-3` | `#FA4616` orange | cars; report pins |
| 4 | `--mbl-chart-4` | `#8F433C` marron, saturated | public transport |

Marine and marron as given read as grey when they're data (too little
colour to tell apart at a glance), so charts use more saturated steps
of the same hues. Blue is pale against white (2.0:1), so no chart
relies on colour alone: every bar has its mode's name beside it, or a
legend and a table. Bar tracks are white with a bleu-gris edge, and
the pale blue bar has a thin darker outline, so a short bar still
shows where it ends.

## The logo

Files in `core/static/core/img/`, made from the association's logo kit
(design Simon Papon, 2020) with the white background card removed and
cropped to the drawing:

| File | For |
|---|---|
| `mobilitains-logo.svg` | light backgrounds: rouge-gris path, marine wordmark |
| `mobilitains-logo-on-dark.svg` | dark backgrounds (the header): white path and wordmark |
| `mobilitains-mark.svg` | the mark alone, light backgrounds; the favicon |
| `mobilitains-mark-on-dark.svg` | the mark alone, dark backgrounds (the header on phones) |

From the guide and the *charte couleur*:

- Rouge-gris is the association's main colour and the heart of the
  logo; the logo appears in colour, or in white, or in black. If
  either the path or the wordmark is white (or black), the other must
  be too.
- Keep the clear space around the logo that the guide shows; don't
  squeeze it against other elements.
- The logo goes at the top (left, or right when needed), never only at
  the bottom of a page.

## Typography

The brand's typeface, Montserrat, is the site's font. It's served from
`core/static/core/fonts/montserrat/` (variable weight, Latin and Latin
Extended, upright and italic; SIL Open Font License in `OFL.txt`)
rather than Google Fonts, so visitors' browsers don't contact a third
party. The Latin upright file is preloaded; the others load only when
needed.

Montserrat is wider than most system fonts. The phone header in
French ("Se déconnecter") fits at 360px with only a few pixels to
spare: check it if you add anything there.

## In the code

- **Use the tokens.** Stylesheets use `var(--mbl-…)`; templates use the
  Bootstrap classes, which `mobilitains.css` themes. Don't write hex
  values anywhere else.
- **Exceptions**, each with a comment naming the token: the map pin
  colours in `core/static/core/js/map_widget.js` (Leaflet needs values,
  not variables), the sign-in email (mail clients ignore CSS
  variables), and `templates/500.html` (it loads no stylesheet).
- **Tests** (`BrandColourTests` in `core/tests.py`) enforce this: no
  hex colours outside the tokens file and those exceptions; the
  exceptions use token values only; the map pins match the legend;
  and every text/background pair listed there meets WCAG AA. Add new
  pairs to that list when you use them. (The scan only looks for hex
  values; shadows in `rgba()` are allowed.)
- **Adding a colour:** don't, if one of these will do. If you must, add
  it to `mobilitains.css` and this page, with its contrast against
  what it will sit on.
- Check contrast with any WCAG checker; check chart palettes with the
  dataviz validator.

## Not covered yet

- **The guide's secondary font** ("Source code", a monospace for
  minor text) isn't used; nothing on the site calls for it yet.
- **Dark mode.** The site has none; if it gets one, its colours need
  choosing and checking separately, not inverting.
- **Orange's meaning.** The *charte couleur* reserves orange for
  membership and donations. Mobilito asks for neither, so we use the
  dark orange for errors. If a "join" or "donate" link is added, it
  should be orange, and errors may then need rethinking.
