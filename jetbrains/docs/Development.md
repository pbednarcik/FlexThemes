# Development

How the themes are built and checked. For installing and using them, see the [README](../README.md).

## Plugins

| Plugin | Build | Source |
|---|---|---|
| FlexThemes (bundle of all below) | `python tools/build.py` | `src/bundle/` |
| Morning Mist | `python tools/build.py --plugin MorningMist` | `src/` |
| Lavender Mist (Gold, Indigo, Blue) | `python tools/build.py --plugin LavenderMist` | `src/lavender/` |
| Blue Mist | `python tools/build.py --plugin BlueMist` | `src/bluemist/` |
| Periwinkle Islands | `python tools/build.py --plugin PeriwinkleIslands` | `src/periwinkle/` |
| Graphite | `python tools/build.py --plugin Graphite` | `src/vsdark/` |
| Periwinkle Night | `python tools/build.py --plugin PeriwinkleNight` | `src/periwinklenight/` |
| Abyss | `python tools/build.py --plugin Abyss` | `src/abyss/` |

Jars are written to `dist/`. `plugins.json` lists the standalone plugins and `bundle.json` the bundle; every plugin
shares the version in `VERSION`. Each plugin's newest `CHANGELOG.md` section becomes its change notes (Morning Mist
uses `jetbrains/CHANGELOG.md`, the others the one in their source folder).

Each source folder holds `theme/*.theme-map.json`, `scheme/*.scheme-map.json`, `vs-values/` (a snapshot of the Visual
Studio values in use), UI contracts, `palette-baseline.json`, and `META-INF/` with the descriptor template and icon.

## Building

`tools/build.py` extracts JetBrains' base themes, base schemes, UI key metadata and the platform default scheme from
the installed Rider into a scratch folder (`--scratch`, default the system temp folder). It checks the language
declarations of the four installed IDEs, generates every theme and scheme with `tools/generate.py`, and packs each
jar itself with fixed entry dates, order and attributes, so the same inputs give the same jar on every system.
No Gradle, and no installed IDE or setting is changed.

The bundle is built from the finished standalone jars. `tools/bundle.py` rechecks each one and requires the bundle to
contain exactly their theme and scheme files, the licence files, one descriptor, an icon and a small manifest of
the included releases. The bundle declares the standalone plugin IDs incompatible, so the same theme cannot be
installed twice. After a successful bundle build, older release jars of the known plugins in `dist/` are removed;
personal builds and unrelated files are left alone.

The build also writes reports (colour tables, language and UI verification) to `build/reports/`. They are
working evidence for checking a change, and are not committed.

## Maps

A theme map and a scheme map name Rider keys and give each a colour expression:

| Expression | Meaning |
|---|---|
| `#RRGGBB`, `#RRGGBBAA` | literal |
| `vs:Category\|Name`, `vs:Category\|Name\|fg` | background or foreground of a decoded Visual Studio colour |
| `vs:...@NN` | the same colour with alpha NN (0 to 255) |
| `$token` | another colour token of the same theme map |
| `A over B [over C]` | alpha compositing, the way Visual Studio paints its Fluent fills |

Map-level fields:

| Field | Meaning |
|---|---|
| `extends` | a map (path relative to this one) this map is laid over; sections merge key by key, and `null` removes an entry |
| `schemeMap` | the scheme map a theme uses (default: the same file stem) |
| `snapshot` | the name of the Visual Studio value snapshot in `vs-values/` shared by maps that read the same data |
| `vsBase`, `vsTint` | which decoded Visual Studio theme the `vs:` references read |
| `sourceNote` | the source paragraph at the top of the colour table |
| `defaultOn` | scheme maps: switches the inherited-colour check on, against this editor background |
| `platformDefault` | scheme maps: the platform default scheme inherited from, `Default` (light) or `Darcula` (dark) |
| `fillRuleWaiver` | scheme maps: skip the caret-line and selection rule, with the reason |
| `contrastExempt` | scheme maps: attributes the inherited check skips, each with its reason |

Entry fields:

| Field | Meaning |
|---|---|
| `status` | `measured`, `vs-data`, `vs-default`, `ms-blue`, `inferred`, `designed`, `inherited` or `user-export` |
| `element` | what the colour paints, and why it has this value |
| `measured`, `sample` | the pixel value sampled in a screenshot; the build fails if the expression resolves to anything else |
| `on` | this colour is text or an icon on that background, checked against `minContrast` |
| `under` | this colour is a fill with that text or icon on it, checked the same way |
| `minContrast` | default 4.5 (WCAG 2.2 text); 3 for icons and focus indicators |
| `minLc` | an additional APCA minimum, for text where the WCAG ratio overstates readability |
| `contrastWaiver` | keep a colour below the minimum on purpose, with the reason |
| `raw` | a non-colour value (sizes, alphas), with `vsValue` saying what was measured |

Personal overrides use the same format; see [overrides.example.json](overrides.example.json).

## Shared base

Fixes that belong in every theme live once in `src/common/`:

- `theme/Common.theme-base.json`: unfocused-window fade, tab name opacity, the gap before the line numbers, the
  project colour gradients of the title row, check boxes in the theme accent.
- `scheme/Common.scheme-base.json`: editor colours the themes would otherwise inherit unchecked (console output,
  inlay hints, AI suggestions, XML, debugger values and more), and the attributes exempt from the contrast check,
  each with its reason.
- The dark themes extend `theme/CommonDark.theme-base.json` and `scheme/CommonDark.scheme-base.json` as well.
- `language-mappings.json` assigns semantic roles (keyword, type, method, string, comment and so on) to the
  highlighting keys of C#, Java, Kotlin, Go and Python once; each scheme's `semanticRoles` gives the roles its colours.

After adding a shared fix, add a check for it to `tools/verify_jars.py`, which reports any built plugin that lacks it.

## Checks

The build fails when:

- a Rider UI key, colour token or scheme attribute is unknown to the installed Rider (Rider ignores unknown keys
  silently), or a UI key is marked deprecated;
- a language highlighting key is missing from the installed IDEs' own declarations;
- a `vs:` colour does not exist in the decoded data or the snapshot, or a `measured` entry resolves to anything
  other than its sample;
- a scheme value is translucent;
- a text colour is below 4.5:1 (or an icon below 3:1) against the surface it is declared on, unless waived with a
  reason;
- in a scheme with `defaultOn`: an inherited foreground is below 4.5:1 and neither mapped nor exempt, a foreground
  falls below 4.5:1 on the caret line or 3:1 inside a selection, or plain text falls below 4.5:1 on a highlight;
- a packaged editor scheme differs from its frozen `palette-baseline.json`, or a theme breaks its UI contract
  (resolved UI values, panel separation and text contrast).

On dark backgrounds the WCAG ratio overstates readability. Where a map sets `minLc`, the build also checks the APCA
Lc; the designed dark themes' code colours were chosen to reach Lc 60 or more, with comments and Code Vision
deliberately quieter.

`tools/check_guards.py` plants one fault per check and passes only when every build fails for that reason; a check
that cannot fail checks nothing. The unit tests run it:

```bash
python tools/check_guards.py . <scratch>/rider-<build> data/vs-<version> <scratch> build/language-evidence.json
```

### Changing a palette

Editor baselines are never refreshed by a build. For an intentional change, build, look at the result in the IDEs,
then update the scheme's `palette-baseline.json` and the theme's UI contract on purpose. A new IDE version that
changes an inherited colour stops the build for review instead of silently changing a released palette.

## Design choices

- **Dark window frame.** JetBrains' [Supporting Islands Theme](https://plugins.jetbrains.com/docs/intellij/supporting-islands-theme.html)
  guidance asks dark themes for a main window lighter than the tool windows. Graphite, Periwinkle Night and
  Abyss keep the frame darker than the tool windows instead, by the guidance's minimum contrast of 1.20:1 and in the
  tool windows' own hue, which reads calmer.
- **Island outline.** All themes draw a visible outline around the panels rather than hiding it, because the panels
  read better with it.

## Visual Studio data

- Morning Mist and Graphite read Visual Studio 2026's own theme data (its Cool Breeze and Dark themes), decoded with
  `tools/decode_vs_theme.py <VS install> data/vs-<version>` (`python tools/build.py --refresh-vs` does this on
  Windows).
- The designed themes are inspired by the Visual Studio 2019/2022 **Blue** theme, which Visual Studio 2026 no longer
  ships. Its theme data is decoded with the same tool from a Visual Studio 2022 install into
  `data/ms-blue-17/Blue.json`.

Without decoded data the build uses the committed snapshots in `vs-values/`, which hold exactly the values in use.

## Tests and tools

```bash
python -m unittest discover -s tools -p "test_*.py"
ruff check tools
python tools/verify_jars.py dist
```

On a fresh clone the tests that need built jars or the installed IDEs are skipped with that reason; build once to
run them all.

| Tool | Purpose |
|---|---|
| `tools/build.py` | builds and verifies the plugins |
| `tools/generate.py` | maps to theme JSON, scheme XML and the reports |
| `tools/check_guards.py` | proves every build check can fail |
| `tools/check_pack.py` | checks a packaged plugin: coverage, language roles, UI contracts |
| `tools/bundle.py` | builds and checks the bundle |
| `tools/verify_jars.py` | checks every built plugin for every shared fix |
| `tools/decode_vs_theme.py` | decodes Visual Studio's binary theme records |
| `tools/extract_vssettings_colours.py` | reads Fonts and Colors from a `.vssettings` export, for personal overrides |
