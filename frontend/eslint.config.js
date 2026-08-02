// path: frontend/eslint.config.js
import js from '@eslint/js';
import tseslint from 'typescript-eslint';

export default tseslint.config(
  { ignores: ['dist', 'node_modules'] },
  js.configs.recommended,
  ...tseslint.configs.recommended,
  {
    languageOptions: {
      ecmaVersion: 2020,
      globals: {
        window: 'readonly',
        document: 'readonly',
        performance: 'readonly',
        localStorage: 'readonly',
        requestAnimationFrame: 'readonly',
        AudioContext: 'readonly',
        HTMLElement: 'readonly',
        HTMLInputElement: 'readonly',
        HTMLCanvasElement: 'readonly',
        KeyboardEvent: 'readonly',
        MouseEvent: 'readonly',
        WheelEvent: 'readonly',
        AudioBuffer: 'readonly',
        AudioNode: 'readonly',
        AudioBufferSourceNode: 'readonly',
        GainNode: 'readonly',
      },
    },
    rules: {
      // The renderer and the audio graph both need escape hatches for browser APIs that
      // TypeScript models loosely; keep them as warnings so they stay visible.
      '@typescript-eslint/no-explicit-any': 'warn',
      '@typescript-eslint/no-unused-vars': [
        'error',
        { argsIgnorePattern: '^_', varsIgnorePattern: '^_' },
      ],
      'no-empty': ['error', { allowEmptyCatch: true }],
    },
  },
);
