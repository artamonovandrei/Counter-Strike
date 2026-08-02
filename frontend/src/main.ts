// path: frontend/src/main.ts
//
// Entry point: owns the menu ↔ game transition and nothing else.

import './style.css';
import { Game } from './game';
import { Menu, type MenuSettings } from './menu';

const appEl = document.getElementById('app');
const canvasEl = document.getElementById('view') as HTMLCanvasElement | null;
const overlayEl = document.getElementById('overlay');

if (!appEl || !canvasEl || !overlayEl) {
  throw new Error('index.html is missing #app, #view or #overlay');
}

// Re-bind after the guard: TypeScript doesn't carry narrowing into the function
// declarations below, and `!` everywhere reads worse than two lines here.
const canvas = canvasEl;
const overlay = overlayEl;

const menu = new Menu(overlay);
let game: Game | null = null;

function teardown(): void {
  game?.destroy();
  game = null;
  // The HUD lives inside #overlay alongside the menu, so clear it explicitly rather than
  // wiping the overlay (which would take the menu with it).
  overlay.querySelectorAll('.hud').forEach((el) => el.remove());
}

menu.onPlay = async (settings: MenuSettings) => {
  const instance = new Game(canvas, overlay, settings);
  game = instance;

  instance.onPauseRequested = () => menu.renderPause();
  instance.onFatal = (message) => {
    teardown();
    menu.showError(message);
  };

  try {
    await instance.connect();
    menu.hide();
  } catch (err) {
    teardown();
    menu.showError(err instanceof Error ? err.message : 'Could not connect.');
  }
};

menu.onResume = () => {
  menu.hide();
  game?.resume();
};

menu.onQuit = () => {
  teardown();
  menu.renderMain();
};

menu.onSettingsChange = (settings) => {
  game?.applySettings(settings);
};

// Escape while paused re-opens the pause menu rather than doing nothing, so the pointer
// lock prompt and the menu never disagree about what state we're in.
window.addEventListener('keydown', (e) => {
  if (e.key === 'Escape' && game && !menu.visible) {
    game.pause();
  }
});

window.addEventListener('beforeunload', () => teardown());
