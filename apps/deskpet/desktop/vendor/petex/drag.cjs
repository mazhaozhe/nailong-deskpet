const DRAG_THRESHOLD = 6;
const SECTOR = Math.PI / 8;
const TURN_MARGIN = 5 * Math.PI / 180;

class DragTracker {
  constructor(cursor, bounds, now) {
    this.origin = {...cursor};
    this.last = {...cursor};
    this.offset = {x: cursor.x - bounds.x, y: cursor.y - bounds.y};
    this.lastTime = now;
    this.lastMotion = now;
    this.dragging = false;
    this.velocity = null;
    this.sector = null;
    this.direction = 'running-right';
  }
  update(cursor, now) {
    const dx = cursor.x - this.last.x;
    const dy = cursor.y - this.last.y;
    const dt = Math.max(1, Math.min(100, now - this.lastTime));
    const fromStart = {x: cursor.x - this.origin.x, y: cursor.y - this.origin.y};
    const justStarted = !this.dragging && Math.hypot(fromStart.x, fromStart.y) >= DRAG_THRESHOLD;
    if (justStarted) this.dragging = true;
    if (this.dragging && (justStarted || Math.hypot(dx, dy) >= 0.75)) {
      const movement = justStarted ? fromStart : {x: dx, y: dy};
      const length = Math.hypot(movement.x, movement.y);
      const velocity = {x: movement.x / length, y: movement.y / length};
      // Smooth the heading, not speed: a fast prior movement must not dominate a slow turn.
      const resumed = now - this.lastMotion > 100 && length >= 2;
      const blend = 1 - Math.exp(-dt / 30);
      this.velocity = this.velocity && !resumed ? {
        x: this.velocity.x + (velocity.x - this.velocity.x) * blend,
        y: this.velocity.y + (velocity.y - this.velocity.y) * blend,
      } : velocity;
      this.lastMotion = now;
      const angle = (Math.atan2(this.velocity.x, -this.velocity.y) + Math.PI * 2) % (Math.PI * 2);
      const difference = this.sector === null ? Infinity : Math.abs(Math.atan2(Math.sin(angle - this.sector * SECTOR), Math.cos(angle - this.sector * SECTOR)));
      if (difference > SECTOR / 2 + TURN_MARGIN) this.sector = Math.round(angle / SECTOR) % 16;
      const horizontal = Math.sin(this.sector * SECTOR);
      if (horizontal > 0.2) this.direction = 'running-right';
      else if (horizontal < -0.2) this.direction = 'running-left';
    }
    this.last = {...cursor}; this.lastTime = now;
    return {
      dragging: this.dragging,
      position: {x: Math.round(cursor.x - this.offset.x), y: Math.round(cursor.y - this.offset.y)},
      direction: this.direction,
      gaze: this.sector === null ? null : {x: Math.sin(this.sector * SECTOR) * 200, y: -Math.cos(this.sector * SECTOR) * 200},
    };
  }
}
module.exports = {DragTracker, DRAG_THRESHOLD};
