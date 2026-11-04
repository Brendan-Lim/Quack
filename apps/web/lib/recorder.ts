// Push-to-talk capture. Meta's transcription endpoint accepts mono 16-bit PCM WAV at 16 or
// 24 kHz, and browsers record neither, so the audio is resampled and encoded here.

export const TARGET_SAMPLE_RATE = 24000;
const MAX_SECONDS = 15;

export function resample(input: Float32Array, fromRate: number, toRate: number): Float32Array {
  if (fromRate === toRate) return input;
  const length = Math.round((input.length * toRate) / fromRate);
  const output = new Float32Array(length);
  const step = fromRate / toRate;
  for (let i = 0; i < length; i++) {
    const position = i * step;
    const index = Math.floor(position);
    const next = Math.min(index + 1, input.length - 1);
    output[i] = input[index] + (input[next] - input[index]) * (position - index);
  }
  return output;
}

export function encodeWav(samples: Float32Array, sampleRate: number): ArrayBuffer {
  const buffer = new ArrayBuffer(44 + samples.length * 2);
  const view = new DataView(buffer);
  const text = (offset: number, value: string) => {
    for (let i = 0; i < value.length; i++) view.setUint8(offset + i, value.charCodeAt(i));
  };
  text(0, "RIFF");
  view.setUint32(4, 36 + samples.length * 2, true);
  text(8, "WAVE");
  text(12, "fmt ");
  view.setUint32(16, 16, true);
  view.setUint16(20, 1, true);
  view.setUint16(22, 1, true);
  view.setUint32(24, sampleRate, true);
  view.setUint32(28, sampleRate * 2, true);
  view.setUint16(32, 2, true);
  view.setUint16(34, 16, true);
  text(36, "data");
  view.setUint32(40, samples.length * 2, true);
  samples.forEach((sample, i) => {
    const clamped = Math.max(-1, Math.min(1, sample));
    view.setInt16(44 + i * 2, clamped < 0 ? clamped * 0x8000 : clamped * 0x7fff, true);
  });
  return buffer;
}

export type Recording = { stop: () => Promise<Blob | null> };

export function stopTracks(stream: MediaStream) {
  stream.getTracks().forEach((track) => track.stop());
}

export function canRecord(): boolean {
  return typeof navigator !== "undefined" && Boolean(navigator.mediaDevices?.getUserMedia);
}

export async function startRecording(cancelled: () => boolean): Promise<Recording | null> {
  const stream = await navigator.mediaDevices.getUserMedia({ audio: { channelCount: 1, echoCancellation: true } });
  // The button may have been released while the permission prompt was up.
  if (cancelled()) {
    stopTracks(stream);
    return null;
  }
  const context = new AudioContext();
  await context.resume();
  const source = context.createMediaStreamSource(stream);
  // Deprecated, but it is the one capture path that works across Safari and Chromium
  // without shipping a separate worklet file.
  const processor = context.createScriptProcessor(4096, 1, 1);
  const chunks: Float32Array[] = [];
  let captured = 0;
  const limit = MAX_SECONDS * context.sampleRate;
  processor.onaudioprocess = (event) => {
    if (captured >= limit) return;
    const chunk = new Float32Array(event.inputBuffer.getChannelData(0));
    chunks.push(chunk);
    captured += chunk.length;
  };
  source.connect(processor);
  processor.connect(context.destination);

  return {
    stop: async () => {
      processor.disconnect();
      source.disconnect();
      stopTracks(stream);
      const rate = context.sampleRate;
      await context.close();
      if (!captured) return null;
      const joined = new Float32Array(captured);
      let offset = 0;
      for (const chunk of chunks) {
        joined.set(chunk, offset);
        offset += chunk.length;
      }
      return new Blob([encodeWav(resample(joined, rate, TARGET_SAMPLE_RATE), TARGET_SAMPLE_RATE)], { type: "audio/wav" });
    },
  };
}
