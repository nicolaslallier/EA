// ESLint, flat config: Vue 3 and TypeScript, with the type-aware rules on.
//
// JavaScript and not TypeScript on purpose: a `.ts` config would have to belong
// to a tsconfig project for the type-aware rules below to parse it, and no
// project here should grow to include the linter's own configuration.
import { defineConfigWithVueTs, vueTsConfigs } from '@vue/eslint-config-typescript'
import { globalIgnores } from 'eslint/config'
import pluginVue from 'eslint-plugin-vue'

export default defineConfigWithVueTs(
  {
    name: 'ea/files-to-lint',
    files: ['**/*.{ts,mts,vue}'],
  },

  // `src/api/` is generated (docs/adr/0007): a finding there is fixed by the
  // generator or not at all.
  globalIgnores(['dist/**', 'coverage/**', 'src/api/**']),

  pluginVue.configs['flat/recommended'],
  vueTsConfigs.recommendedTypeChecked,

  {
    name: 'ea/promises',
    rules: {
      // Both are already in `recommendedTypeChecked`; they are restated because
      // they are the reason the type-aware preset is here at all. A load whose
      // promise nobody awaits is how a stale answer ends up on screen — the
      // class of bug `lib/latest.ts` exists to prevent.
      '@typescript-eslint/no-floating-promises': 'error',
      '@typescript-eslint/no-misused-promises': 'error',
    },
  },

  {
    name: 'ea/conventions',
    rules: {
      // `const { element: _dropped, ...rest } = query` is how a key is left out
      // of a copy; the named sibling exists to be discarded.
      '@typescript-eslint/no-unused-vars': ['error', { ignoreRestSiblings: true }],
      // Every `<input>` here is written `<input ... />`. The preset wants the
      // HTML spelling `<input ...>`; either is correct, and the one in use wins.
      'vue/html-self-closing': ['error', { html: { void: 'always' } }],
    },
  },

  {
    name: 'ea/template-layout',
    rules: {
      // Layout, not correctness. The templates wrap attributes to fit the line
      // rather than one per line, and `flat/recommended` would rewrite every
      // one of them — a diff of thousands of lines that fixes nothing. These
      // five decide where a newline goes and nothing else.
      'vue/max-attributes-per-line': 'off',
      'vue/first-attribute-linebreak': 'off',
      'vue/html-closing-bracket-newline': 'off',
      'vue/singleline-html-element-content-newline': 'off',
      'vue/multiline-html-element-content-newline': 'off',
    },
  },
)
