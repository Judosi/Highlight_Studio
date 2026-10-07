import { execFileSync } from 'node:child_process'
export function verifyProductionBuild() {
  execFileSync(process.env.HS_TEST_PYTHON || 'python', ['tools/diagnostics/verify_release_identity.py', '.'], {
    cwd: new URL('../../', import.meta.url), stdio: 'pipe',
  })
}
