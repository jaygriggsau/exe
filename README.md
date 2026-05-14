# DiskStat

A small WinDirStat-style disk usage analyzer for Windows (also works on
macOS/Linux). Single-file Python 3 app using the standard library only.

## Features

- Pick any folder and scan it in a background thread (cancellable).
- Folder tree with size, percentage of total, and item counts.
- "By extension" breakdown panel, color-coded.
- Squarified treemap: every file is a rectangle, sized by bytes and
  colored by extension. Hover for a tooltip, click to reveal in the
  tree.
- Selecting a folder in the tree highlights all of its files in the
  treemap.

## Run

Requires Python 3.8+ (the Windows installer from python.org includes
the `tkinter` GUI library by default).

```cmd
diskstat.bat               REM opens with no folder loaded
diskstat.bat C:\Users\me   REM scans the given folder on startup
```

Or directly:

```cmd
python diskstat.py [path]
```

## Notes

- Symlinks to directories are not followed, to avoid loops.
- Files we can't `stat` are silently skipped.
- The treemap shows every file in the scanned tree at once. For very
  large scans (millions of files) the redraw can take a second; this is
  expected.
