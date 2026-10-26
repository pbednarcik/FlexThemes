# FlexThemes

[![JetBrains Marketplace](https://img.shields.io/jetbrains/plugin/v/35464?label=JetBrains%20Marketplace)](https://plugins.jetbrains.com/plugin/35464-flexthemes)
[![Downloads](https://img.shields.io/jetbrains/plugin/d/35464)](https://plugins.jetbrains.com/plugin/35464-flexthemes)

FlexThemes is the collection of Visual Studio-inspired themes JetBrains tools have always been missing: calm colors,
clear panel edges and easy-to-read code. JetBrains comes first; Visual Studio and VS Code themes are planned.

![Periwinkle Night in Rider](jetbrains/docs/screenshots/periwinkle-night-rider.png)

*Periwinkle Night in Rider*

## Screenshots

Nine light and dark appearances for Rider, IntelliJ IDEA, GoLand and PyCharm. Real captures from the IDEs, without
edits: C# in Rider, Java in IntelliJ IDEA, Go in GoLand and Python in PyCharm.

| | |
|---|---|
| ![Morning Mist in Rider](jetbrains/docs/screenshots/morning-mist-rider.png)<br>**Morning Mist** · Rider | ![Graphite in Rider](jetbrains/docs/screenshots/graphite-rider.png)<br>**Graphite** · Rider |
| ![Lavender Mist Gold in IntelliJ IDEA](jetbrains/docs/screenshots/lavender-mist-gold-intellij.png)<br>**Lavender Mist Gold** · IntelliJ IDEA | ![Lavender Mist Indigo in IntelliJ IDEA](jetbrains/docs/screenshots/lavender-mist-indigo-intellij.png)<br>**Lavender Mist Indigo** · IntelliJ IDEA |
| ![Lavender Mist Blue in GoLand](jetbrains/docs/screenshots/lavender-mist-blue-goland.png)<br>**Lavender Mist Blue** · GoLand | ![Blue Mist in GoLand](jetbrains/docs/screenshots/blue-mist-goland.png)<br>**Blue Mist** · GoLand |
| ![Periwinkle Islands in PyCharm](jetbrains/docs/screenshots/periwinkle-islands-pycharm.png)<br>**Periwinkle Islands** · PyCharm | ![Abyss in PyCharm](jetbrains/docs/screenshots/abyss-pycharm.png)<br>**Abyss** · PyCharm |

## Get the themes

| Platform | Folder | Status |
|---|---|---|
| JetBrains IDEs | [jetbrains/](jetbrains/README.md) | FlexThemes 0.7.1 on [JetBrains Marketplace](https://plugins.jetbrains.com/plugin/35464-flexthemes): one bundle with every theme. [GitHub Releases](https://github.com/pbednarcik/FlexThemes/releases) also have one plugin per theme family |
| Visual Studio, VS Code | | planned |

The JetBrains themes need a 2026.2 IDE with the Islands UI. They are tested in Rider, IntelliJ IDEA, GoLand and
PyCharm; other JetBrains IDEs based on 2026.2 may work too, but have not been tested. Installing, the theme list and
the settings a theme cannot carry are in the [JetBrains README](jetbrains/README.md).

## Build

From the JetBrains project folder with Python, on Windows, macOS or Linux:

```bash
cd jetbrains
python tools/build.py
```

The build reads the installed IDEs; the [JetBrains README](jetbrains/README.md#build) lists what it needs.

## License

Apache-2.0; see [LICENSE](LICENSE). Platform projects retain their own attribution notices, including [jetbrains/NOTICE](jetbrains/NOTICE).
