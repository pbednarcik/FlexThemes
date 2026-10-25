# Changelog: Morning Mist

## 0.7.1

- No theme changes; version aligned with the FlexThemes bundle.

## 0.7.0

- First public release: Morning Mist, a light theme with the colours of Visual Studio 2026's Cool Breeze theme: a tinted window, light rounded panels with clear outlines and Visual Studio's C# syntax colours.
- Colours are decoded from Visual Studio's own theme data and checked against screenshots; a build writes a colour table that traces every value to its source.
- Shared language colours for C#, Java, Kotlin, Go and Python, checked against the installed IDEs' own declarations.
- Every text colour is checked for contrast when the theme is built.
- Requires JetBrains IDEs 2026.2 or later with the Islands (new) UI. Tested in Rider, IntelliJ IDEA, GoLand and PyCharm 2026.2.3 on Windows.
