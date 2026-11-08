import js from '@eslint/js'
import globals from 'globals'
import reactHooks from 'eslint-plugin-react-hooks'
import reactRefresh from 'eslint-plugin-react-refresh'
import tseslint from 'typescript-eslint'
import { defineConfig, globalIgnores } from 'eslint/config'

export default defineConfig([
  globalIgnores(['dist']),
  {
    files: ['**/*.{ts,tsx}'],
    extends: [
      js.configs.recommended,
      tseslint.configs.recommended,
      reactHooks.configs.flat.recommended,
      reactRefresh.configs.vite,
    ],
    languageOptions: {
      ecmaVersion: 2020,
      globals: globals.browser,
    },
  },
  // shadcn/ui primitives + auth/router files mix component + non-component
  // exports by design. The fast-refresh rule trips on the pattern, so opt
  // them out — they're stable infrastructure, not the place where HMR speed
  // matters most.
  {
    files: [
      'src/components/ui/**/*.{ts,tsx}',
      'src/lib/auth.tsx',
      'src/router.tsx',
    ],
    rules: {
      'react-refresh/only-export-components': 'off',
    },
  },
])
