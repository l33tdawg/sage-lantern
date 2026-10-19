// Pure state machine shared by the compositor guard and synthetic race tests.
// There is no bearer credential in this object and no browser status input.
export class GuardState {
    constructor(conceal, reveal, now) {
        this.conceal = conceal;
        this.reveal = reveal;
        this.now = now;
        this.generation = 0;
        this.authorized = false;
        this.deadline = 0;
        this.lastNow = 0;
        this.mode = 'kiosk';
        this.lock();
    }

    lock() {
        this.generation++;
        this.authorized = false;
        this.deadline = 0;
        this.conceal();
    }

    setMode(mode) {
        if (!['kiosk', 'desktop'].includes(mode))
            throw new Error('invalid_mode');
        this.lock(); // Before any asynchronous request, rotation or browser replacement.
        this.mode = mode;
        return this.generation;
    }

    accept(stamp, reply, mayReveal = false) {
        if (stamp !== this.generation)
            return false; // A response from before relock cannot affect the successor.
        const now = this.now();
        if (!Number.isFinite(now) || now < this.lastNow ||
            (this.authorized && now >= this.deadline)) {
            this.lock();
            return false;
        }
        this.lastNow = now;
        if (reply?.ok !== true || reply.result?.authorized !== true) {
            this.lock();
            return false;
        }
        // Presence cannot reveal a locked screen. Only this generation's PIN reply can.
        if (!this.authorized && !mayReveal)
            return false;
        this.deadline = now + 3500;
        if (!this.authorized) {
            this.authorized = true;
            try {
                this.reveal();
            } catch {
                this.lock();
                return false;
            }
        }
        return true;
    }

    tick() {
        const now = this.now();
        if (!Number.isFinite(now) || now < this.lastNow ||
            (this.authorized && now >= this.deadline))
            this.lock();
        this.lastNow = now;
    }
}
