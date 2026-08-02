// path: frontend/src/net.ts
//
// Socket.IO client. Two connections, matching the server's two namespaces: /lobby to get
// a ticket, /game to play. The lobby socket is dropped once the ticket is redeemed —
// there's nothing to keep it open for, and an idle socket is one more thing to reconnect.

import { io, Socket } from 'socket.io-client';
import {
  NS_GAME,
  NS_LOBBY,
  PROTOCOL_VERSION,
  type GameEvent,
  type InputCmd,
  type MatchFoundResponse,
  type PrimaryWeaponId,
  type Snapshot,
  type Team,
  type Welcome,
} from '@shared/protocol';

export interface NetHandlers {
  onWelcome(w: Welcome): void;
  onSnapshot(s: Snapshot): void;
  onEvent(e: GameEvent): void;
  onDisconnect(reason: string): void;
  onError(message: string): void;
}

/** Where to connect. In dev, Vite proxies these paths to the backend on :8000. */
const SERVER_URL = import.meta.env.VITE_SERVER_URL || window.location.origin;

export class NetClient {
  private lobby: Socket | null = null;
  private game: Socket | null = null;
  private handlers: NetHandlers | null = null;

  /** Round-trip time in ms as measured by the server and echoed back in snapshots. */
  ping = 0;
  /** Snapshots received since connect — used by the netgraph to spot stalls. */
  snapshotCount = 0;
  /** Server time (ms) of the newest snapshot, and when we received it locally. */
  lastServerTime = 0;
  lastServerTimeAt = 0;
  connected = false;

  setHandlers(h: NetHandlers): void {
    this.handlers = h;
  }

  /**
   * Ask the lobby for a match. Resolves with a one-shot ticket.
   *
   * Rejects rather than retrying: a failure here is almost always "server full" or
   * "version mismatch", both of which need the player to see a message, not a spinner.
   */
  findMatch(
    name: string,
    team: Team | null,
    primary: PrimaryWeaponId | null,
  ): Promise<MatchFoundResponse> {
    return new Promise((resolve, reject) => {
      const socket = io(`${SERVER_URL}${NS_LOBBY}`, {
        transports: ['websocket', 'polling'],
        timeout: 10000,
      });
      this.lobby = socket;

      const fail = (msg: string) => {
        socket.close();
        this.lobby = null;
        reject(new Error(msg));
      };

      const timer = window.setTimeout(() => fail('The server did not respond.'), 12000);

      socket.on('connect', () => {
        socket.emit('find_match', { protocol: PROTOCOL_VERSION, name, team, primary });
      });

      socket.on('match_found', (data: MatchFoundResponse) => {
        window.clearTimeout(timer);
        socket.close();
        this.lobby = null;
        if (!data?.ok || !data.ticket) {
          reject(new Error(data?.error || 'Could not find a match.'));
          return;
        }
        resolve(data);
      });

      socket.on('connect_error', () => {
        window.clearTimeout(timer);
        fail('Cannot reach the server. Is the backend running?');
      });
    });
  }

  /** Connect to the game namespace and redeem the ticket. Resolves on `welcome`. */
  joinGame(ticket: string): Promise<Welcome> {
    return new Promise((resolve, reject) => {
      const socket = io(`${SERVER_URL}${NS_GAME}`, {
        transports: ['websocket', 'polling'],
        timeout: 10000,
        reconnection: false, // a dropped match is over; the menu handles rejoining
      });
      this.game = socket;
      let settled = false;

      socket.on('connect', () => {
        this.connected = true;
        socket.emit('join', { protocol: PROTOCOL_VERSION, ticket });
      });

      socket.on('welcome', (w: Welcome) => {
        settled = true;
        this.handlers?.onWelcome(w);
        resolve(w);
      });

      socket.on('join_error', (data: { error: string }) => {
        if (!settled) reject(new Error(data?.error || 'Join refused.'));
        else this.handlers?.onError(data?.error || 'Join refused.');
      });

      socket.on('connect_error', () => {
        if (!settled) reject(new Error('Cannot reach the game server.'));
      });

      socket.on('snap', (s: Snapshot) => {
        this.snapshotCount++;
        this.lastServerTime = s.st;
        this.lastServerTimeAt = performance.now();
        if (typeof s.self?.pg === 'number') this.ping = s.self.pg;
        this.handlers?.onSnapshot(s);
      });

      socket.on('ev', (events: GameEvent[]) => {
        if (!Array.isArray(events)) return;
        for (const e of events) {
          // Answer the server's RTT probe immediately — any delay here inflates the
          // measured ping, which in turn widens this player's lag-compensation window.
          if (e.e === 'ping_req') {
            socket.emit('ping_ack', { i: e.i });
            continue;
          }
          this.handlers?.onEvent(e);
        }
      });

      socket.on('disconnect', (reason: string) => {
        this.connected = false;
        this.handlers?.onDisconnect(reason);
      });
    });
  }

  sendInput(cmd: InputCmd): void {
    this.game?.volatile.emit('input', cmd);
  }

  /**
   * Send several commands at once.
   *
   * `volatile` is deliberate: an input that arrives late is worse than useless, because
   * the server has already simulated past it. Dropping it beats queueing it behind a
   * congested socket.
   */
  sendInputBatch(cmds: InputCmd[]): void {
    if (cmds.length === 0) return;
    if (cmds.length === 1) this.sendInput(cmds[0]);
    else this.game?.volatile.emit('input_batch', { c: cmds });
  }

  sendChat(msg: string): void {
    this.game?.emit('chat', { msg });
  }

  dropWeapon(): void {
    this.game?.emit('drop');
  }

  disconnect(): void {
    this.game?.close();
    this.lobby?.close();
    this.game = null;
    this.lobby = null;
    this.connected = false;
  }
}

/** Buffers snapshots so remote entities can be rendered in the interpolated past. */
export class SnapshotBuffer {
  private buffer: Snapshot[] = [];
  private capacity = 32;

  push(s: Snapshot): void {
    this.buffer.push(s);
    // Out-of-order arrival is possible on the polling transport; keep it sorted so the
    // interpolation search below can assume monotonic time.
    if (this.buffer.length > 1 && s.st < this.buffer[this.buffer.length - 2].st) {
      this.buffer.sort((a, b) => a.st - b.st);
    }
    while (this.buffer.length > this.capacity) this.buffer.shift();
  }

  /** The two snapshots bracketing `renderTime`, plus the blend factor between them. */
  pair(renderTime: number): { from: Snapshot; to: Snapshot; alpha: number } | null {
    if (this.buffer.length === 0) return null;
    if (this.buffer.length === 1) {
      const only = this.buffer[0];
      return { from: only, to: only, alpha: 0 };
    }
    for (let i = this.buffer.length - 1; i > 0; i--) {
      const to = this.buffer[i];
      const from = this.buffer[i - 1];
      if (from.st <= renderTime && renderTime <= to.st) {
        const span = to.st - from.st;
        return { from, to, alpha: span > 0 ? (renderTime - from.st) / span : 0 };
      }
    }
    // renderTime is outside the buffer: extrapolating looks worse than freezing on the
    // nearest end, so clamp.
    if (renderTime < this.buffer[0].st) {
      const first = this.buffer[0];
      return { from: first, to: first, alpha: 0 };
    }
    const last = this.buffer[this.buffer.length - 1];
    return { from: last, to: last, alpha: 0 };
  }

  latest(): Snapshot | null {
    return this.buffer.length ? this.buffer[this.buffer.length - 1] : null;
  }

  clear(): void {
    this.buffer.length = 0;
  }
}
