export const MAX_CAMERA_FRAME_BYTES = 2 * 1024 * 1024;

export class CameraFrameDecoder {
  private header = new Uint8Array(4);
  private headerUsed = 0;
  private frame: Uint8Array | null = null;
  private frameUsed = 0;

  push(chunk: Uint8Array): Uint8Array[] {
    const frames: Uint8Array[] = [];
    let offset = 0;
    while (offset < chunk.length) {
      if (!this.frame) {
        const count = Math.min(4 - this.headerUsed, chunk.length - offset);
        this.header.set(chunk.subarray(offset, offset + count), this.headerUsed);
        this.headerUsed += count;
        offset += count;
        if (this.headerUsed < 4) continue;
        const size = new DataView(this.header.buffer).getUint32(0);
        if (size < 4 || size > MAX_CAMERA_FRAME_BYTES) throw new Error('Invalid camera frame size');
        this.frame = new Uint8Array(size);
        this.frameUsed = 0;
      }
      const count = Math.min(this.frame.length - this.frameUsed, chunk.length - offset);
      this.frame.set(chunk.subarray(offset, offset + count), this.frameUsed);
      offset += count;
      this.frameUsed += count;
      if (this.frameUsed === this.frame.length) {
        const frame = this.frame;
        if (frame[0] !== 255 || frame[1] !== 216 || frame[frame.length - 2] !== 255 || frame[frame.length - 1] !== 217) throw new Error('Invalid camera image');
        frames.push(frame);
        this.frame = null;
        this.headerUsed = 0;
      }
    }
    return frames;
  }

  finish(): void {
    if (this.frame || this.headerUsed) throw new Error('Incomplete camera frame');
  }
}
