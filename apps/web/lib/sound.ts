// The quack is synthesised, so there is no audio asset to license. Browsers only allow audio
// after a user gesture, so `unlock` is called from one; if that has not happened the
// alert is purely visual.

let context: AudioContext | null = null;

export function unlockAudio(): void {
  if (typeof window === "undefined") return;
  if (!context) {
    const Constructor = window.AudioContext ?? (window as unknown as { webkitAudioContext?: typeof AudioContext }).webkitAudioContext;
    if (!Constructor) return;
    context = new Constructor();
  }
  // Safari can refuse outside a gesture it counts. That's no error worth reporting: it stays silent.
  if (context.state === "suspended") context.resume().catch(() => {});
}

export function audioReady(): boolean {
  return context?.state === "running";
}

export function playQuack(): boolean {
  if (!context || context.state !== "running") return false;
  const start = context.currentTime;
  const output = context.createGain();
  output.gain.setValueAtTime(0.0001, start);
  output.gain.exponentialRampToValueAtTime(0.5, start + 0.015);
  output.gain.setValueAtTime(0.5, start + 0.07);
  output.gain.exponentialRampToValueAtTime(0.0001, start + 0.2);
  output.connect(context.destination);

  // Two resonances over a buzzy source give the nasal, honking vowel.
  const source = context.createOscillator();
  source.type = "sawtooth";
  source.frequency.setValueAtTime(330, start);
  source.frequency.exponentialRampToValueAtTime(235, start + 0.18);
  for (const [frequency, q, gain] of [
    [950, 7, 1],
    [1900, 9, 0.45],
  ]) {
    const filter = context.createBiquadFilter();
    filter.type = "bandpass";
    filter.frequency.setValueAtTime(frequency * 1.15, start);
    filter.frequency.exponentialRampToValueAtTime(frequency * 0.85, start + 0.18);
    filter.Q.value = q;
    const level = context.createGain();
    level.gain.value = gain;
    source.connect(filter);
    filter.connect(level);
    level.connect(output);
  }
  source.start(start);
  source.stop(start + 0.22);
  return true;
}

// A question answered right: two soft rising notes, quieter than the quack and over in a third of a
// second, so it says "well done" without asking for attention. Silent until a gesture has unlocked audio.
export function playSuccess(): boolean {
  if (!context || context.state !== "running") return false;
  const start = context.currentTime;
  for (const [frequency, offset] of [
    [659.25, 0],
    [987.77, 0.11],
  ]) {
    const tone = context.createOscillator();
    tone.type = "sine";
    tone.frequency.setValueAtTime(frequency, start + offset);
    const level = context.createGain();
    level.gain.setValueAtTime(0.0001, start + offset);
    level.gain.exponentialRampToValueAtTime(0.16, start + offset + 0.02);
    level.gain.exponentialRampToValueAtTime(0.0001, start + offset + 0.22);
    tone.connect(level);
    level.connect(context.destination);
    tone.start(start + offset);
    tone.stop(start + offset + 0.24);
  }
  return true;
}
