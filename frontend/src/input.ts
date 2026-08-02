// path: frontend/src/input.ts
//
// Keyboard, mouse and pointer lock. Produces the key bitmask the protocol expects.
//
// Mouse look is accumulated from raw movementX/Y events and applied once per frame rather
// than per event: browsers can deliver several mouse events between frames, and applying
// each one separately makes high-polling-rate mice feel jittery.

import {
  K_BACK,
  K_CROUCH,
  K_FIRE,
  K_FORWARD,
  K_JUMP,
  K_LEFT,
  K_RELOAD,
  K_RIGHT,
  K_SPRINT,
} from '@shared/protocol';

const HALF_PI = Math.PI / 2;

export interface InputSettings {
  sensitivity: number;
  invertY: boolean;
}

export class InputManager {
  private pressed = new Set<string>();
  private mouseDown = false;
  private pendingYaw = 0;
  private pendingPitch = 0;

  yaw = 0;
  pitch = 0;
  locked = false;
  /** Weapon slot requested this frame (1..3), or 0. Cleared once consumed. */
  weaponRequest = 0;

  settings: InputSettings = { sensitivity: 1.0, invertY: false };

  onLockChange: ((locked: boolean) => void) | null = null;
  onToggleScoreboard: ((visible: boolean) => void) | null = null;
  onChat: (() => void) | null = null;
  onDrop: (() => void) | null = null;
  onNetGraph: (() => void) | null = null;

  /** True while the player is typing, so gameplay keys must be ignored. */
  textEntryActive = false;

  constructor(private canvas: HTMLElement) {
    this.bind();
  }

  private bind(): void {
    // Clicking the canvas re-acquires the pointer. This matters more than it looks:
    // browsers only grant pointer lock during a user gesture, and the gesture that
    // started the match (clicking "Deploy") has expired by the time the server's welcome
    // arrives. Without this, a player can end up looking at the world unable to move.
    this.canvas.addEventListener('mousedown', this.onCanvasClick);
    window.addEventListener('keydown', this.onKeyDown);
    window.addEventListener('keyup', this.onKeyUp);
    window.addEventListener('mousedown', this.onMouseDown);
    window.addEventListener('mouseup', this.onMouseUp);
    window.addEventListener('mousemove', this.onMouseMove);
    window.addEventListener('wheel', this.onWheel, { passive: true });
    document.addEventListener('pointerlockchange', this.onPointerLockChange);
    // Losing focus mid-key leaves that key stuck down forever otherwise.
    window.addEventListener('blur', this.releaseAll);
  }

  dispose(): void {
    this.canvas.removeEventListener('mousedown', this.onCanvasClick);
    window.removeEventListener('keydown', this.onKeyDown);
    window.removeEventListener('keyup', this.onKeyUp);
    window.removeEventListener('mousedown', this.onMouseDown);
    window.removeEventListener('mouseup', this.onMouseUp);
    window.removeEventListener('mousemove', this.onMouseMove);
    window.removeEventListener('wheel', this.onWheel);
    document.removeEventListener('pointerlockchange', this.onPointerLockChange);
    window.removeEventListener('blur', this.releaseAll);
  }

  requestLock(): void {
    // Chrome rejects this with an unhandled promise rejection when it isn't inside a
    // user gesture; swallow it and let the canvas click below pick up the slack.
    if (this.locked) return;
    const result = this.canvas.requestPointerLock() as unknown as Promise<void> | undefined;
    if (result && typeof result.catch === 'function') result.catch(() => undefined);
  }

  private onCanvasClick = (): void => {
    if (!this.textEntryActive) this.requestLock();
  };

  exitLock(): void {
    if (this.locked) document.exitPointerLock();
  }

  private releaseAll = (): void => {
    this.pressed.clear();
    this.mouseDown = false;
  };

  private onPointerLockChange = (): void => {
    this.locked = document.pointerLockElement === this.canvas;
    if (!this.locked) this.releaseAll();
    this.onLockChange?.(this.locked);
  };

  private onKeyDown = (e: KeyboardEvent): void => {
    if (e.code === 'Tab') e.preventDefault(); // Tab would move focus off the canvas
    if (this.textEntryActive) return;

    if (!this.pressed.has(e.code)) {
      switch (e.code) {
        case 'Digit1':
          this.weaponRequest = 1;
          break;
        case 'Digit2':
          this.weaponRequest = 2;
          break;
        case 'Digit3':
          this.weaponRequest = 3;
          break;
        case 'Tab':
          this.onToggleScoreboard?.(true);
          break;
        case 'KeyY':
          this.onChat?.();
          break;
        case 'KeyG':
          this.onDrop?.();
          break;
        case 'F3':
          e.preventDefault();
          this.onNetGraph?.();
          break;
        default:
          break;
      }
    }
    this.pressed.add(e.code);
  };

  private onKeyUp = (e: KeyboardEvent): void => {
    this.pressed.delete(e.code);
    if (e.code === 'Tab') this.onToggleScoreboard?.(false);
  };

  private onMouseDown = (e: MouseEvent): void => {
    if (e.button === 0) this.mouseDown = true;
  };

  private onMouseUp = (e: MouseEvent): void => {
    if (e.button === 0) this.mouseDown = false;
  };

  private onMouseMove = (e: MouseEvent): void => {
    if (!this.locked) return;
    // 0.0022 rad per count at sensitivity 1 lands close to the 400 DPI / 2.0 in-game
    // feel most players are used to.
    const scale = 0.0022 * this.settings.sensitivity;
    this.pendingYaw -= e.movementX * scale;
    this.pendingPitch -= e.movementY * scale * (this.settings.invertY ? -1 : 1);
  };

  private onWheel = (e: WheelEvent): void => {
    if (!this.locked || this.textEntryActive) return;
    this.weaponRequest = e.deltaY > 0 ? -1 : -2; // resolved against the current weapon
  };

  /** Fold accumulated mouse motion into the view angles. Call once per frame. */
  applyMouse(): void {
    if (this.pendingYaw === 0 && this.pendingPitch === 0) return;
    this.yaw = wrapAngle(this.yaw + this.pendingYaw);
    this.pitch = clamp(this.pitch + this.pendingPitch, -HALF_PI + 0.001, HALF_PI - 0.001);
    this.pendingYaw = 0;
    this.pendingPitch = 0;
  }

  /** Add a server-sent recoil kick to the view, exactly as a real weapon would. */
  applyKick(yaw: number, pitch: number): void {
    this.yaw = wrapAngle(this.yaw + yaw);
    this.pitch = clamp(this.pitch + pitch, -HALF_PI + 0.001, HALF_PI - 0.001);
  }

  keyMask(): number {
    if (this.textEntryActive) return 0;
    let mask = 0;
    if (this.pressed.has('KeyW') || this.pressed.has('ArrowUp')) mask |= K_FORWARD;
    if (this.pressed.has('KeyS') || this.pressed.has('ArrowDown')) mask |= K_BACK;
    if (this.pressed.has('KeyA') || this.pressed.has('ArrowLeft')) mask |= K_LEFT;
    if (this.pressed.has('KeyD') || this.pressed.has('ArrowRight')) mask |= K_RIGHT;
    if (this.pressed.has('Space')) mask |= K_JUMP;
    if (this.pressed.has('ShiftLeft') || this.pressed.has('ShiftRight')) mask |= K_SPRINT;
    if (this.pressed.has('ControlLeft') || this.pressed.has('KeyC')) mask |= K_CROUCH;
    if (this.pressed.has('KeyR')) mask |= K_RELOAD;
    if (this.mouseDown && this.locked) mask |= K_FIRE;
    return mask;
  }

  consumeWeaponRequest(currentSlot: number): number {
    const req = this.weaponRequest;
    this.weaponRequest = 0;
    if (req > 0) return req;
    if (req === -1) return (currentSlot % 3) + 1; // wheel down: next
    if (req === -2) return ((currentSlot + 1) % 3) + 1; // wheel up: previous
    return 0;
  }
}

export function clamp(v: number, lo: number, hi: number): number {
  return v < lo ? lo : v > hi ? hi : v;
}

export function wrapAngle(a: number): number {
  const t = Math.PI * 2;
  a = (a + Math.PI) % t;
  if (a < 0) a += t;
  return a - Math.PI;
}
