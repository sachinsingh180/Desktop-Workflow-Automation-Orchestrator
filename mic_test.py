import sounddevice as sd
import numpy as np

print("=== INPUT (Microphone) devices only ===")
devices = sd.query_devices()
for i, d in enumerate(devices):
    if d['max_input_channels'] > 0:
        print(f"{i}: {d['name']}  (in={d['max_input_channels']}, default_sr={d['default_samplerate']})")

print()

device_index = int(input("Device number type karo: "))
device_info = sd.query_devices(device_index)
sr_to_use = int(device_info['default_samplerate'])
print(f"Using samplerate: {sr_to_use}")

print(f"Recording 3 seconds on device {device_index}... bolo kuch loudly!")
recording = sd.rec(
    int(3 * sr_to_use),
    samplerate=sr_to_use,
    channels=1,
    dtype='int16',
    device=device_index,
)
sd.wait()
print(f"Max amplitude: {np.abs(recording).max()}  (1000+ hona chahiye agar sahi mic hai)")