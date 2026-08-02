// path: frontend/src/game.ts
//
// Orchestration: owns the renderer, the fixed-rate input loop, and the wiring between
// network events and everything that reacts to them.
//
// Two clocks run here. Input is sampled at exactly the server's tick rate via an
// accumulator, so a 144 Hz monitor doesn't flood the server and a 30 Hz one doesn't
// starve it. Rendering runs at whatever rate the browser offers.

import * as THREE from 'three';
import {
  F_DEAD,
  F_RELOADING,
  K_FIRE,
  TEAM_NAMES,
  type GameEvent,
  type InputCmd,
  type Snapshot,
  type Team,
  type Welcome,
  type WeaponConfig,
  type WeaponId,
} from '@shared/protocol';
import { AudioEngine, spatialise } from './audio';
import { Effects } from './effects';
import { HUD } from './hud';
import { InputManager } from './input';
import type { MenuSettings } from './menu';
import { NetClient, SnapshotBuffer } from './net';
import { Predictor } from './predict';
import { RemotePlayers } from './remote';
import { applyFog, buildLevel, buildLights, buildSky, CollisionWorld } from './world';

const MAX_FRAME_DT = 0.1; // never simulate more than 100 ms of catch-up in one frame

export class Game {
  private renderer: THREE.WebGLRenderer;
  private scene = new THREE.Scene();
  private camera: THREE.PerspectiveCamera;

  private net = new NetClient();
  private snapshots = new SnapshotBuffer();
  private input: InputManager;
  private hud: HUD | null = null;
  private effects: Effects | null = null;
  private remotes: RemotePlayers | null = null;
  private predictor: Predictor | null = null;
  private world: CollisionWorld | null = null;
  private audio = new AudioEngine();

  private welcome: Welcome | null = null;
  private weapons = new Map<WeaponId, WeaponConfig>();
  private myId = 0;
  private myTeam: Team = 'A';

  private inputSeq = 0;
  private accumulator = 0;
  private tickDt = 1 / 60;
  private outgoing: InputCmd[] = [];

  private running = false;
  private paused = false;
  private lastFrame = 0;
  private fps = 60;
  private fpsAccum = 0;
  private fpsFrames = 0;

  private currentWeapon: WeaponId = 'rifle';
  private lastLocalShotAt = 0;
  private prevFireHeld = false;
  private prevYaw = 0;
  private prevPitch = 0;
  private wasDead = false;
  private lastPhase = '';
  private selfAmmo = 0;
  private selfReloading = false;

  onExit: (() => void) | null = null;
  onFatal: ((message: string) => void) | null = null;

  constructor(
    canvas: HTMLCanvasElement,
    private overlay: HTMLElement,
    private settings: MenuSettings,
  ) {
    this.renderer = new THREE.WebGLRenderer({
      canvas,
      antialias: true,
      powerPreference: 'high-performance',
    });
    this.renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
    this.renderer.setSize(window.innerWidth, window.innerHeight);
    this.renderer.outputColorSpace = THREE.SRGBColorSpace;

    this.camera = new THREE.PerspectiveCamera(
      settings.fov,
      window.innerWidth / window.innerHeight,
      0.05,
      500,
    );
    this.camera.rotation.order = 'YXZ';

    this.input = new InputManager(canvas);
    this.input.settings = { sensitivity: settings.sensitivity, invertY: settings.invertY };
    this.audio.setVolume(settings.volume);

    window.addEventListener('resize', this.onResize);
    document.addEventListener('visibilitychange', this.onVisibility);
  }

  // ── lifecycle ───────────────────────────────────────────────────────────────

  async connect(): Promise<void> {
    this.net.setHandlers({
      onWelcome: (w) => this.onWelcome(w),
      onSnapshot: (s) => this.onSnapshot(s),
      onEvent: (e) => this.onEvent(e),
      onDisconnect: (reason) => this.onDisconnect(reason),
      onError: (msg) => this.hud?.toast(msg, 4000),
    });

    const match = await this.net.findMatch(this.settings.name || 'Recruit', this.settings.team);
    await this.net.joinGame(match.ticket!);
  }

  private onWelcome(w: Welcome): void {
    this.welcome = w;
    this.myId = w.playerId;
    this.myTeam = w.team;
    this.tickDt = 1 / w.config.tickHz;
    for (const weapon of w.weapons) this.weapons.set(weapon.id, weapon);

    // ── scene ────────────────────────────────────────────────────────────────
    this.scene.clear();
    this.scene.add(buildSky(w.map));
    this.scene.add(buildLevel(w.map));
    this.scene.add(buildLights(w.map));
    applyFog(this.scene, w.map);

    this.world = new CollisionWorld(w.map);
    this.predictor = new Predictor(this.world, w.config);
    this.remotes = new RemotePlayers(this.scene, w.team);
    this.effects = new Effects(this.scene, this.camera);
    this.effects.setWeaponVisual('rifle');

    this.hud = new HUD(this.overlay, w.team);
    this.hud.onChatSubmit = (msg) => this.net.sendChat(msg);
    this.hud.onChatOpenChange = (open) => {
      this.input.textEntryActive = open;
      if (open) this.input.exitLock();
      else this.input.requestLock();
    };
    this.hud.addNotice(`Joined ${TEAM_NAMES[w.team]} on ${w.map.name}`);

    this.input.onToggleScoreboard = (v) => this.hud?.setScoreboardVisible(v);
    this.input.onChat = () => this.hud?.openChat();
    this.input.onDrop = () => this.net.dropWeapon();
    this.input.onNetGraph = () => this.hud?.setNetgraphVisible(!this.hud.netgraphVisible);
    this.input.onLockChange = (locked) => {
      if (!locked && !this.hud?.chatOpen) this.pause();
    };

    // Face the way the map's spawn point wants us to.
    this.input.yaw = 0;
    this.input.pitch = 0;

    this.audio.init();
    this.running = true;
    this.lastFrame = performance.now();
    requestAnimationFrame(this.frame);
    this.input.requestLock();
  }

  private onDisconnect(reason: string): void {
    if (!this.running) return;
    this.running = false;
    this.onFatal?.(
      reason === 'io server disconnect'
        ? 'Disconnected by the server.'
        : 'Connection lost. Check your network and try again.',
    );
  }

  pause(): void {
    if (!this.running || this.paused) return;
    this.paused = true;
    this.input.exitLock();
    this.onPauseRequested?.();
  }

  onPauseRequested: (() => void) | null = null;

  resume(): void {
    this.paused = false;
    this.input.requestLock();
  }

  applySettings(s: MenuSettings): void {
    this.settings = s;
    this.input.settings = { sensitivity: s.sensitivity, invertY: s.invertY };
    this.audio.setVolume(s.volume);
    this.camera.fov = s.fov;
    this.camera.updateProjectionMatrix();
  }

  destroy(): void {
    this.running = false;
    window.removeEventListener('resize', this.onResize);
    document.removeEventListener('visibilitychange', this.onVisibility);
    this.input.dispose();
    this.effects?.dispose();
    this.remotes?.dispose();
    this.hud?.destroy();
    this.net.disconnect();
    this.renderer.dispose();
  }

  private onResize = (): void => {
    this.camera.aspect = window.innerWidth / window.innerHeight;
    this.camera.updateProjectionMatrix();
    this.renderer.setSize(window.innerWidth, window.innerHeight);
  };

  private onVisibility = (): void => {
    // Rendering stops when the tab is hidden, but the socket stays open and snapshots
    // keep arriving — disconnecting a backgrounded player would be worse than a few
    // wasted packets.
    if (document.hidden) this.pause();
  };

  // ── network handlers ────────────────────────────────────────────────────────

  private onSnapshot(s: Snapshot): void {
    this.snapshots.push(s);
    const predictor = this.predictor;
    if (!predictor) return;

    const dead = (s.self.f & F_DEAD) !== 0;
    predictor.reconcile(s.self, s.ack, dead);

    this.currentWeapon = s.self.w;
    this.selfAmmo = s.self.am;
    this.selfReloading = (s.self.f & F_RELOADING) !== 0;
    this.effects?.setWeaponVisual(s.self.w);

    const hud = this.hud;
    if (hud) {
      const def = this.weapons.get(s.self.w);
      hud.setVitals(s.self.hp, s.self.ar);
      hud.setAmmo(def?.name ?? s.self.w, s.self.am, s.self.rs, def?.melee ?? false);
      hud.setReloading((s.self.f & F_RELOADING) !== 0);
      hud.setRound(s.ph, s.pt, s.sc.A, s.sc.B);
      hud.setRespawn(dead ? s.self.rt : 0);
    }

    if (dead && !this.wasDead) this.audio.play('death', 0, 0.9);
    if (!dead && this.wasDead) this.audio.play('spawn', 0, 0.7);
    this.wasDead = dead;

    if (s.ph !== this.lastPhase) {
      this.lastPhase = s.ph;
      if (s.ph === 'live') this.hud?.toast('Round start', 1800);
    }
  }

  private onEvent(e: GameEvent): void {
    const hud = this.hud;
    const effects = this.effects;
    const px = this.predictor?.renderX() ?? 0;
    const pz = this.predictor?.renderZ() ?? 0;

    switch (e.e) {
      case 'shot': {
        // Our own shot was already drawn locally the instant we pulled the trigger;
        // drawing the server's echo too would double every muzzle flash.
        if (e.id === this.myId) return;
        const origin = new THREE.Vector3(e.o[0], e.o[1], e.o[2]);
        const dir = new THREE.Vector3(e.d[0], e.d[1], e.d[2]).normalize();
        const range = this.weapons.get(e.w)?.range ?? 100;
        const hit = this.world?.raycast(origin, dir, range);
        const end = hit
          ? hit.point
          : origin.clone().addScaledVector(dir, Math.min(range, 80));
        effects?.tracer(origin, end);
        effects?.muzzleFlash(origin, 0.8);
        const { pan, gain } = spatialise(px, pz, this.input.yaw, e.o[0], e.o[2]);
        this.audio.play(e.w === 'knife' ? 'knife' : e.w, pan, gain);
        break;
      }
      case 'impact': {
        effects?.impact(
          new THREE.Vector3(e.p[0], e.p[1], e.p[2]),
          new THREE.Vector3(e.n[0], e.n[1], e.n[2]),
          e.m,
        );
        break;
      }
      case 'hit': {
        hud?.showHitMarker(e.hs, e.kill);
        this.audio.play(e.hs ? 'headshot' : 'hit', 0, 0.8);
        break;
      }
      case 'hurt': {
        hud?.flashDamage(0.2 + Math.min(0.5, e.amt / 100));
        this.audio.play('hurt', 0, 0.7);
        break;
      }
      case 'kick': {
        // Recoil moves the player's actual view — controlling a spray means pulling back
        // against this, exactly as the server's spread model assumes.
        this.input.applyKick(e.y, e.p);
        break;
      }
      case 'kill': {
        hud?.addKill(e.k, e.v, e.w, e.hs, e.team);
        if (e.vid === this.myId) hud?.toast(`Eliminated by ${e.k}`, 2200);
        break;
      }
      case 'spawn': {
        if (e.id !== this.myId) {
          const { pan, gain } = spatialise(px, pz, this.input.yaw, e.p[0], e.p[2], 40);
          this.audio.play('spawn', pan, gain * 0.4);
        }
        break;
      }
      case 'round': {
        if (e.ph === 'intermission' && e.winner) {
          const label =
            e.winner === 'draw'
              ? 'Round drawn'
              : e.winner === this.myTeam
                ? 'Your team wins the round'
                : `${TEAM_NAMES[e.winner as Team]} wins the round`;
          hud?.toast(label, 4000);
        }
        break;
      }
      case 'join': {
        this.remotes?.setNameFor(e.id, e.name, e.team);
        if (e.id !== this.myId && !e.bot) hud?.addNotice(`${e.name} joined`);
        break;
      }
      case 'leave': {
        this.remotes?.remove(e.id);
        break;
      }
      case 'chat': {
        hud?.addChat(e.name, e.team, e.msg);
        this.audio.play('ui', 0, 0.4);
        break;
      }
      case 'scoreboard': {
        hud?.setScoreboard(e.rows);
        for (const row of e.rows) this.remotes?.setNameFor(row.id, row.name, row.team);
        break;
      }
      case 'switch': {
        if (e.id === this.myId) this.audio.play('switch', 0, 0.5);
        break;
      }
      default:
        break;
    }
  }

  // ── the loop ────────────────────────────────────────────────────────────────

  private frame = (now: number): void => {
    if (!this.running) return;
    requestAnimationFrame(this.frame);

    let dt = (now - this.lastFrame) / 1000;
    this.lastFrame = now;
    if (dt > MAX_FRAME_DT) dt = MAX_FRAME_DT;
    if (dt <= 0) return;

    this.fpsAccum += dt;
    this.fpsFrames++;
    if (this.fpsAccum >= 0.5) {
      this.fps = this.fpsFrames / this.fpsAccum;
      this.fpsAccum = 0;
      this.fpsFrames = 0;
    }

    if (!this.paused) {
      this.input.applyMouse();
      this.effects?.addSway(this.input.yaw - this.prevYaw, this.input.pitch - this.prevPitch);
      this.prevYaw = this.input.yaw;
      this.prevPitch = this.input.pitch;

      this.sampleInputs(dt, now);
    }

    this.predictor?.update(dt);
    this.updateCamera();
    this.updateRemotes(dt, now);
    this.effects?.update(dt);
    this.effects?.updateViewModel(
      dt,
      this.predictor?.speed() ?? 0,
      this.welcome!.config,
      this.predictor?.grounded ?? true,
    );
    this.updateHud(now);

    this.renderer.render(this.scene, this.camera);
  };

  /** Produce input commands at exactly the server tick rate. */
  private sampleInputs(dt: number, now: number): void {
    this.accumulator += dt;
    // Bound the catch-up: a long stall should not release a burst of commands the server
    // would only throw away.
    const maxSteps = 4;
    let steps = 0;
    this.outgoing.length = 0;

    while (this.accumulator >= this.tickDt && steps < maxSteps) {
      this.accumulator -= this.tickDt;
      steps++;

      const keys = this.input.keyMask();
      const slot = this.input.consumeWeaponRequest(this.weapons.get(this.currentWeapon)?.slot ?? 1);

      this.inputSeq++;
      const cmd: InputCmd = {
        s: this.inputSeq,
        dt: this.tickDt * 1000,
        k: keys,
        y: this.input.yaw,
        p: this.input.pitch,
        w: slot,
      };

      this.predictor?.apply(cmd);
      this.outgoing.push(cmd);
      this.predictLocalFire(keys, now);
    }
    if (this.accumulator > this.tickDt * maxSteps) this.accumulator = 0;

    this.net.sendInputBatch(this.outgoing);
  }

  /**
   * Draw and sound our own gunshot immediately rather than waiting for the server echo.
   *
   * The server remains the authority on whether the shot happened and what it hit — this
   * only predicts the *presentation*. Worst case on a mispredict is one extra muzzle
   * flash, which nobody notices; the alternative is a full round-trip of delay between
   * clicking and hearing your own gun, which everybody notices.
   */
  private predictLocalFire(keys: number, now: number): void {
    const def = this.weapons.get(this.currentWeapon);
    const predictor = this.predictor;
    if (!def || !predictor || this.wasDead) {
      this.prevFireHeld = false;
      return;
    }

    const firing = (keys & K_FIRE) !== 0;
    if (!firing) {
      this.prevFireHeld = false;
      return;
    }
    const wasHeld = this.prevFireHeld;
    this.prevFireHeld = true;
    if (!def.auto && wasHeld) return;

    // Don't predict a shot the server is certain to refuse.
    if (this.selfReloading) return;
    if (!def.melee && this.selfAmmo <= 0) {
      if (!wasHeld) this.audio.play('empty', 0, 0.6);
      return;
    }

    const interval = 60000 / def.rpm;
    if (now - this.lastLocalShotAt < interval) return;
    this.lastLocalShotAt = now;

    const eye = new THREE.Vector3(
      predictor.renderX(),
      predictor.renderY() + this.welcome!.config.eyeHeight,
      predictor.renderZ(),
    );
    const dir = new THREE.Vector3(
      -Math.sin(this.input.yaw) * Math.cos(this.input.pitch),
      Math.sin(this.input.pitch),
      -Math.cos(this.input.yaw) * Math.cos(this.input.pitch),
    );

    if (def.melee) {
      this.audio.play('knife', 0, 0.9);
      this.effects?.muzzleFlash(eye, 0.2);
      return;
    }

    const hit = this.world?.raycast(eye, dir, def.range);
    const end = hit ? hit.point : eye.clone().addScaledVector(dir, def.range);
    // Start the tracer at the muzzle, not the eye, or it visibly emerges from your face.
    const muzzle = eye.clone().addScaledVector(dir, 0.55);
    muzzle.y -= 0.12;
    this.effects?.tracer(muzzle, end);
    this.effects?.muzzleFlash(muzzle, 1);
    this.audio.play(this.currentWeapon === 'pistol' ? 'pistol' : 'rifle', 0, 1);
  }

  private updateCamera(): void {
    const p = this.predictor;
    if (!p || !this.welcome) return;
    this.camera.position.set(
      p.renderX(),
      p.renderY() + this.welcome.config.eyeHeight,
      p.renderZ(),
    );
    this.camera.rotation.y = this.input.yaw;
    this.camera.rotation.x = this.input.pitch;
  }

  private updateRemotes(dt: number, now: number): void {
    if (!this.remotes || !this.welcome) return;
    // Render remote entities in the past by the interpolation delay. The server's clock
    // is tracked via the newest snapshot's timestamp plus local elapsed time.
    const elapsed = performance.now() - this.net.lastServerTimeAt;
    const renderTime =
      this.net.lastServerTime + elapsed - this.welcome.config.interpDelayMs;
    const pair = this.snapshots.pair(renderTime);
    if (!pair) return;
    this.remotes.update(pair.from, pair.to, pair.alpha, dt, now);
  }

  private updateHud(now: number): void {
    const hud = this.hud;
    if (!hud) return;
    hud.update(now);

    const def = this.weapons.get(this.currentWeapon);
    if (def && this.predictor) {
      // Mirror the server's spread model closely enough that the crosshair is honest.
      const speed = this.predictor.speed();
      const cfg = this.welcome!.config;
      let spread = def.spreadBase;
      if (!this.predictor.grounded) spread += def.spreadAir;
      else if (speed > 0.1) spread += def.spreadMove * Math.min(1, speed / cfg.sprintSpeed);
      hud.setSpread(4 + spread * 6);
    }

    const latest = this.snapshots.latest();
    hud.setNetgraph({
      fps: this.fps,
      ping: this.net.ping,
      snapshots: this.net.snapshotCount,
      pending: this.predictor?.pendingCount() ?? 0,
      correction: this.predictor?.lastCorrection ?? 0,
      hardSnaps: this.predictor?.hardSnaps ?? 0,
      entities: latest?.ents.length ?? 0,
    });
  }
}
