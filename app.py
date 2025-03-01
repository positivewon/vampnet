from pathlib import Path
from typing import Tuple
import yaml
import tempfile
import uuid
from dataclasses import dataclass, asdict

import numpy as np
import audiotools as at
import argbind

import gradio as gr
from vampnet.interface import Interface
from vampnet import mask as pmask

Interface = argbind.bind(Interface)
# AudioLoader = argbind.bind(at.data.datasets.AudioLoader)

conf = argbind.parse_args()


from torch_pitch_shift import pitch_shift, get_fast_shifts
def shift_pitch(signal, interval: int):
    signal.samples = pitch_shift(
        signal.samples, 
        shift=interval, 
        sample_rate=signal.sample_rate
    )
    return signal

def load_interface():
    with argbind.scope(conf):
        interface = Interface()
        # loader = AudioLoader()
        print(f"interface device is {interface.device}")
        return interface




interface = load_interface()




# dataset = at.data.datasets.AudioDataset(
#     loader,
#     sample_rate=interface.codec.sample_rate,
#     duration=interface.coarse.chunk_size_s,
#     n_examples=5000,
#     without_replacement=True,
# )

OUT_DIR = Path("gradio-outputs")
OUT_DIR.mkdir(exist_ok=True, parents=True)


def load_audio(file):
    print(file)
    filepath = file.name
    sig = at.AudioSignal.salient_excerpt(
        filepath, 
        duration=interface.coarse.chunk_size_s
    )
    sig = interface.preprocess(sig)

    out_dir = OUT_DIR / "tmp" / str(uuid.uuid4())
    out_dir.mkdir(parents=True, exist_ok=True)
    sig.write(out_dir / "input.wav")
    return sig.path_to_file


def load_example_audio():
    return "./assets/example.wav"


def _vamp(data, return_mask=False):

    out_dir = OUT_DIR / str(uuid.uuid4())
    out_dir.mkdir()
    sig = at.AudioSignal(data[input_audio])
    sig = interface.preprocess(sig)

    if data[pitch_shift_amt] != 0:
        sig = shift_pitch(sig, data[pitch_shift_amt])

    z = interface.encode(sig)

    ncc = data[n_conditioning_codebooks]

    # build the mask
    mask = pmask.linear_random(z, data[rand_mask_intensity])
    mask = pmask.mask_and(
        mask, pmask.inpaint(
            z,
            interface.s2t(data[prefix_s]),
            interface.s2t(data[suffix_s])
        )
    )
    mask = pmask.mask_and(
        mask, pmask.periodic_mask(
            z,
            data[periodic_p],
            data[periodic_w],
            random_roll=True
        )
    )
    if data[onset_mask_width] > 0:
        mask = pmask.mask_or(
            mask, pmask.onset_mask(sig, z, interface, width=data[onset_mask_width])
        )
    if data[beat_mask_width] > 0:
        beat_mask = interface.make_beat_mask(
            sig,
            after_beat_s=(data[beat_mask_width]/1000), 
            mask_upbeats=not data[beat_mask_downbeats],
        )
        mask = pmask.mask_and(mask, beat_mask)

    # these should be the last two mask ops
    mask = pmask.dropout(mask, data[dropout])
    mask = pmask.codebook_unmask(mask, ncc)


    print(f"dropout {data[dropout]}")
    print(f"masktemp {data[masktemp]}")
    print(f"sampletemp {data[sampletemp]}")
    print(f"top_p {data[top_p]}")
    print(f"prefix_s {data[prefix_s]}")
    print(f"suffix_s {data[suffix_s]}")
    print(f"rand_mask_intensity {data[rand_mask_intensity]}")
    print(f"num_steps {data[num_steps]}")
    print(f"periodic_p {data[periodic_p]}")
    print(f"periodic_w {data[periodic_w]}")
    print(f"n_conditioning_codebooks {data[n_conditioning_codebooks]}")
    print(f"use_coarse2fine {data[use_coarse2fine]}")
    print(f"onset_mask_width {data[onset_mask_width]}")
    print(f"beat_mask_width {data[beat_mask_width]}")
    print(f"beat_mask_downbeats {data[beat_mask_downbeats]}")
    print(f"stretch_factor {data[stretch_factor]}")
    print(f"seed {data[seed]}")
    print(f"pitch_shift_amt {data[pitch_shift_amt]}")
    print(f"sample_cutoff {data[sample_cutoff]}")
    
    
    _top_p = data[top_p] if data[top_p] > 0 else None
    # save the mask as a txt file
    np.savetxt(out_dir / "mask.txt", mask[:,0,:].long().cpu().numpy())

    _seed = data[seed] if data[seed] > 0 else None
    zv, mask_z = interface.coarse_vamp(
        z, 
        mask=mask,
        sampling_steps=data[num_steps],
        mask_temperature=data[masktemp]*10,
        sampling_temperature=data[sampletemp],
        return_mask=True, 
        typical_filtering=data[typical_filtering], 
        typical_mass=data[typical_mass], 
        typical_min_tokens=data[typical_min_tokens], 
        top_p=_top_p,
        gen_fn=interface.coarse.generate,
        seed=_seed,
        sample_cutoff=data[sample_cutoff],
    )

    if use_coarse2fine: 
        zv = interface.coarse_to_fine(
            zv, 
            mask_temperature=data[masktemp]*10, 
            sampling_temperature=data[sampletemp],
            mask=mask,
            sampling_steps=data[num_steps],
            sample_cutoff=data[sample_cutoff], 
            seed=_seed,
        )

    sig = interface.to_signal(zv).cpu()
    print("done")

    

    sig.write(out_dir / "output.wav")

    if return_mask:
        mask = interface.to_signal(mask_z).cpu()
        mask.write(out_dir / "mask.wav")
        return sig.path_to_file, mask.path_to_file
    else:
        return sig.path_to_file

def vamp(data):
    return _vamp(data, return_mask=True)

def api_vamp(data):
    return _vamp(data, return_mask=False)
        
def save_vamp(data):
    out_dir = OUT_DIR / "saved" / str(uuid.uuid4())
    out_dir.mkdir(parents=True, exist_ok=True)

    sig_in = at.AudioSignal(data[input_audio])
    sig_out = at.AudioSignal(data[output_audio])

    sig_in.write(out_dir / "input.wav")
    sig_out.write(out_dir / "output.wav")
    
    _data = {
        "masktemp": data[masktemp],
        "sampletemp": data[sampletemp],
        "top_p": data[top_p],
        "prefix_s": data[prefix_s],
        "suffix_s": data[suffix_s],
        "rand_mask_intensity": data[rand_mask_intensity],
        "num_steps": data[num_steps],
        "notes": data[notes_text],
        "periodic_period": data[periodic_p],
        "periodic_width": data[periodic_w],
        "n_conditioning_codebooks": data[n_conditioning_codebooks], 
        "use_coarse2fine": data[use_coarse2fine],
        "stretch_factor": data[stretch_factor],
        "seed": data[seed],
        "samplecutoff": data[sample_cutoff],
    }

    # save with yaml
    with open(out_dir / "data.yaml", "w") as f:
        yaml.dump(_data, f)

    import zipfile
    zip_path = out_dir.with_suffix(".zip")
    with zipfile.ZipFile(zip_path, "w") as zf:
        for file in out_dir.iterdir():
            zf.write(file, file.name)

    return f"saved! your save code is {out_dir.stem}", zip_path



with gr.Blocks() as demo:

    with gr.Row():
        with gr.Column():
            gr.Markdown("# VampNet Audio Vamping")
            gr.Markdown("""## Description:
            This is a demo of the VampNet, a generative audio model that transforms the input audio based on the chosen settings. 
            You can control the extent and nature of variation with a set of manual controls and presets. 
            Use this interface to experiment with different mask settings and explore the audio outputs.
            """)

            gr.Markdown("""
            ## Instructions:
            1. You can start by uploading some audio, or by loading the example audio. 
            2. Choose a preset for the vamp operation, or manually adjust the controls to customize the mask settings. 
            3. Click the "generate (vamp)!!!" button to apply the vamp operation. Listen to the output audio.
            4. Optionally, you can add some notes and save the result. 
            5. You can also use the output as the new input and continue experimenting!
            """)
    with gr.Row():
        with gr.Column():


            manual_audio_upload = gr.File(
                label=f"upload some audio (will be randomly trimmed to max of {interface.coarse.chunk_size_s:.2f}s)",
                file_types=["audio"]
            )
            load_example_audio_button = gr.Button("or load example audio")

            input_audio = gr.Audio(
                label="input audio",
                interactive=False, 
                type="filepath",
            )

            audio_mask = gr.Audio(
                label="audio mask (listen to this to hear the mask hints)",
                interactive=False, 
                type="filepath",
            )

            # connect widgets
            load_example_audio_button.click(
                fn=load_example_audio,
                inputs=[],
                outputs=[ input_audio]
            )

            manual_audio_upload.change(
                fn=load_audio,
                inputs=[manual_audio_upload],
                outputs=[ input_audio]
            )
                
        # mask settings
        with gr.Column():


            presets = {
                    "unconditional": {
                        "periodic_p": 0,
                        "onset_mask_width": 0,
                        "beat_mask_width": 0,
                        "beat_mask_downbeats": False,
                    }, 
                    "slight periodic variation": {
                        "periodic_p": 5,
                        "onset_mask_width": 5,
                        "beat_mask_width": 0,
                        "beat_mask_downbeats": False,
                    },
                    "moderate periodic variation": {
                        "periodic_p": 13,
                        "onset_mask_width": 5,
                        "beat_mask_width": 0,
                        "beat_mask_downbeats": False,
                    },
                    "strong periodic variation": {
                        "periodic_p": 17,
                        "onset_mask_width": 5,
                        "beat_mask_width": 0,
                        "beat_mask_downbeats": False,
                    },
                    "very strong periodic variation": {
                        "periodic_p": 21,
                        "onset_mask_width": 5,
                        "beat_mask_width": 0,
                        "beat_mask_downbeats": False,
                    },
                    "beat-driven variation": {
                        "periodic_p": 0,
                        "onset_mask_width": 0,
                        "beat_mask_width": 50,
                        "beat_mask_downbeats": False,
                    },
                    "beat-driven variation (downbeats only)": {
                        "periodic_p": 0,
                        "onset_mask_width": 0,
                        "beat_mask_width": 50,
                        "beat_mask_downbeats": True,
                    },
                    "beat-driven variation (downbeats only, strong)": {
                        "periodic_p": 0,
                        "onset_mask_width": 0,
                        "beat_mask_width": 20,
                        "beat_mask_downbeats": True,
                    },
                }

            preset = gr.Dropdown(
                label="preset", 
                choices=list(presets.keys()),
                value="strong periodic variation",
            )
            load_preset_button = gr.Button("load_preset")

            with gr.Accordion("manual controls", open=True):
                periodic_p = gr.Slider(
                    label="periodic prompt  (0 - unconditional, 2 - lots of hints, 8 - a couple of hints, 16 - occasional hint, 32 - very occasional hint, etc)",
                    minimum=0,
                    maximum=128, 
                    step=1,
                    value=3, 
                )


                onset_mask_width = gr.Slider(
                    label="onset mask width (multiplies with the periodic mask, 1 step ~= 10milliseconds) ",
                    minimum=0,
                    maximum=100,
                    step=1,
                    value=5,
                )

                beat_mask_width = gr.Slider(
                    label="beat mask width (in milliseconds)",
                    minimum=0,
                    maximum=200,
                    value=0,
                )
                beat_mask_downbeats = gr.Checkbox(
                    label="beat mask downbeats only?", 
                    value=False
                )


                with gr.Accordion("extras ", open=False):
                    pitch_shift_amt = gr.Slider(
                        label="pitch shift amount (semitones)",
                        minimum=-12,
                        maximum=12,
                        step=1,
                        value=0,
                    )

                    rand_mask_intensity = gr.Slider(
                        label="random mask intensity. (If this is less than 1, scatters prompts throughout the audio, should be between 0.9 and 1.0)",
                        minimum=0.0,
                        maximum=1.0,
                        value=1.0
                    )

                    periodic_w = gr.Slider(
                        label="periodic prompt width (steps, 1 step ~= 10milliseconds)",
                        minimum=1,
                        maximum=20,
                        step=1,
                        value=1,
                    )
                    n_conditioning_codebooks = gr.Number(
                        label="number of conditioning codebooks. probably 0", 
                        value=0,
                        precision=0,
                    )

                    stretch_factor = gr.Slider(
                        label="time stretch factor",
                        minimum=0,
                        maximum=64, 
                        step=1,
                        value=1, 
                    )

            preset_outputs = {
                periodic_p, 
                onset_mask_width, 
                beat_mask_width,
                beat_mask_downbeats,
            }

            def load_preset(_preset):
                return tuple(presets[_preset].values())

            load_preset_button.click(
                fn=load_preset,
                inputs=[preset],
                outputs=preset_outputs
            )


            with gr.Accordion("prefix/suffix prompts", open=False):
                prefix_s = gr.Slider(
                    label="prefix hint length (seconds)",
                    minimum=0.0,
                    maximum=10.0,
                    value=0.0
                )
                suffix_s = gr.Slider(
                    label="suffix hint length (seconds)",
                    minimum=0.0,
                    maximum=10.0,
                    value=0.0
                )

            masktemp = gr.Slider(
                label="mask temperature",
                minimum=0.0,
                maximum=100.0,
                value=1.5
            )
            sampletemp = gr.Slider(
                label="sample temperature",
                minimum=0.1,
                maximum=10.0,
                value=1.0, 
                step=0.001
            )
        


            with gr.Accordion("sampling settings", open=False):
                top_p = gr.Slider(
                    label="top p (0.0 = off)",
                    minimum=0.0,
                    maximum=1.0,
                    value=0.0
                )
                typical_filtering = gr.Checkbox(
                    label="typical filtering ",
                    value=False
                )
                typical_mass = gr.Slider( 
                    label="typical mass (should probably stay between 0.1 and 0.5)",
                    minimum=0.01,
                    maximum=0.99,
                    value=0.15
                )
                typical_min_tokens = gr.Slider(
                    label="typical min tokens (should probably stay between 1 and 256)",
                    minimum=1,
                    maximum=256,
                    step=1,
                    value=64
                )
                sample_cutoff = gr.Slider(
                    label="sample cutoff",
                    minimum=0.0,
                    maximum=1.0,
                    value=0.5, 
                    step=0.01
                )

            use_coarse2fine = gr.Checkbox(
                label="use coarse2fine",
                value=True, 
                visible=False
            )

            num_steps = gr.Slider(
                label="number of steps (should normally be between 12 and 36)",
                minimum=1,
                maximum=128,
                step=1,
                value=36
            )

            dropout = gr.Slider(
                label="mask dropout",
                minimum=0.0,
                maximum=1.0,
                step=0.01,
                value=0.0
            )


            seed = gr.Number(
                label="seed (0 for random)",
                value=0,
                precision=0,
            )



        # mask settings
        with gr.Column():

            # lora_choice = gr.Dropdown(
            #     label="lora choice", 
            #     choices=list(loras.keys()),
            #     value=LORA_NONE, 
            #     visible=False
            # )

            vamp_button = gr.Button("generate (vamp)!!!")
            output_audio = gr.Audio(
                label="output audio",
                interactive=False,
                type="filepath"
            )

            notes_text = gr.Textbox(
                label="type any notes about the generated audio here", 
                value="",
                interactive=True
            )
            save_button = gr.Button("save vamp")
            download_file = gr.File(
                label="vamp to download will appear here",
                interactive=False
            )
            use_as_input_button = gr.Button("use output as input")
            
            thank_you = gr.Markdown("")


    _inputs = {
            input_audio, 
            num_steps,
            masktemp,
            sampletemp,
            top_p,
            prefix_s, suffix_s, 
            rand_mask_intensity, 
            periodic_p, periodic_w,
            n_conditioning_codebooks, 
            dropout,
            use_coarse2fine, 
            stretch_factor, 
            onset_mask_width, 
            typical_filtering,
            typical_mass,
            typical_min_tokens,
            beat_mask_width,
            beat_mask_downbeats,
            seed, 
            # lora_choice,
            pitch_shift_amt, 
            sample_cutoff
        }
  
    # connect widgets
    vamp_button.click(
        fn=vamp,
        inputs=_inputs,
        outputs=[output_audio, audio_mask], 
    )

    api_vamp_button = gr.Button("api vamp", visible=False)
    api_vamp_button.click(
        fn=api_vamp,
        inputs=_inputs, 
        outputs=[output_audio], 
        api_name="vamp"
    )

    use_as_input_button.click(
        fn=lambda x: x,
        inputs=[output_audio],
        outputs=[input_audio]
    )

    save_button.click(
        fn=save_vamp,
        inputs=_inputs | {notes_text, output_audio},
        outputs=[thank_you, download_file]
    )

demo.launch(share=True, enable_queue=True, debug=True)
