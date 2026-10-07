# Desktop packaging

The supported release path is `electron/`.

- `electron/` — production hybrid shell, native file dialogs and installer configuration;
- `tauri/` — experimental future scaffold, not used for the 11.2.7 release.

The desktop shell contains no editing logic. It launches the local engine and provides a small, isolated native bridge to the React UI.
