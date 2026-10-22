# OpenMDict dictionary sources

Source code for the preset dictionaries of the OpenMDict iOS app that are
licensed under the GNU GPL or AGPL:

| File in OpenMDict | License | Built from |
|---|---|---|
| `FreeDict-EN-DE.mdx` | GPL-3.0-or-later AND AGPL-3.0-or-later | [FreeDict](https://freedict.org/) eng-deu 1.9-fd1 |
| `FreeDict-DE-EN.mdx` | GPL-3.0-or-later AND AGPL-3.0-or-later | [FreeDict](https://freedict.org/) deu-eng 1.9-fd1 |
| `GCIDE-EN.mdx` | GPL-3.0-or-later | [GCIDE](https://www.gnu.org/software/gcide/) 0.53 |

- `scripts/`: the scripts that convert the upstream data to MDict.
- The [Releases](../../releases) of this repository: unmodified copies of the
  upstream source archives (`freedict-eng-deu.src.tar.xz`,
  `freedict-deu-eng.src.tar.xz`, `gcide-0.53.tar.xz`). FreeDict's own source
  form is the Ding dictionary (GPLv2+) and the ding2tei-haskell program
  (AGPLv3+), as its TEI header names.
- `SHA256SUMS`: the SHA-256 of each file as OpenMDict ships it.

Each tag of this repository corresponds to a set of dictionary files shipped
by OpenMDict.

## Building

Requirements: Python 3.10+, [mdict-utils](https://github.com/liuyug/mdict-utils)
1.3.14 (`pip install mdict-utils==1.3.14`), curl, and tar with xz support.

```
make
```

downloads the upstream archives into `data/` and verifies them, builds
`output/<Name>/<Name>.mdx`, and checks that each file is byte-identical to the
one OpenMDict ships. To build offline, put the archives from the Release in
`data/freedict/` and `data/gcide/` first.

## Licenses

- The dictionaries: FreeDict under the GNU GPL v3 and the GNU AGPL v3, each
  applying to different parts of the combined work; GCIDE under the GNU GPL v3
  or later. The texts are in `licenses/`; the upstream archives carry their own
  copyright notices.
- The scripts in this repository: MIT, see `LICENSE`.

These dictionaries are distributed WITHOUT ANY WARRANTY; without even the
implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.
