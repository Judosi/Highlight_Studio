# Electron hybrid desktop shell

Electron is the release desktop path for v10.12.0.

At startup it:

1. selects a safe loopback port;
2. reuses only a compatible Highlight Studio backend;
3. launches `resources/engine/HighlightStudioEngine.exe` in packaged mode;
4. passes user-writable data and project directories;
5. waits for the exact compatible `/api/health` response;
6. opens the React UI served by the local engine.

The preload bridge exposes only:

- native video selection;
- project directory selection;
- opening an existing file/folder;
- revealing an existing file;
- read-only desktop runtime information.

Renderer Node.js access is disabled. IPC requests are accepted only from the current local application origin.

## Development

```bash
npm ci
npm test
npm start
```

The source backend must be available for `npm start`.

## Windows installer

Use the repository-level script:

```powershell
scripts\windows\build_hybrid_release.ps1
```

It builds the React frontend, standalone engine, NSIS installer and portable EXE.
