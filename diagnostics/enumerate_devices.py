import pyaudio
import speech_recognition as sr

def main():
    p = pyaudio.PyAudio()
    print("=== PyAudio Default Devices ===")
    try:
        def_in = p.get_default_input_device_info()
        print(f"Default Input Device: Index {def_in['index']} - {def_in['name']} (SampleRate: {def_in['defaultSampleRate']})")
    except Exception as e:
        print(f"No default input device: {e}")

    print("\n=== All Input Devices in PyAudio ===")
    for i in range(p.get_device_count()):
        info = p.get_device_info_by_index(i)
        if info.get("maxInputChannels", 0) > 0:
            api_info = p.get_host_api_info_by_index(info["hostApi"])
            print(f"Index {i:2d}: {info['name']} | HostAPI: {api_info['name']} | Channels: {info['maxInputChannels']} | DefaultRate: {info['defaultSampleRate']}")

    print("\n=== SpeechRecognition Microphone Names ===")
    for idx, name in enumerate(sr.Microphone.list_microphone_names()):
        print(f"SR Index {idx:2d}: {name}")

    p.terminate()

if __name__ == "__main__":
    main()

