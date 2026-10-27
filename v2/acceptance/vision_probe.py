"""Explicit real-provider visual challenge; creates no phone input."""
import argparse
import asyncio
import hashlib
import json
from pathlib import Path
import secrets
from PIL import Image,ImageDraw,ImageFont
from digitallife_v2.brain import LifeBrain
from digitallife_v2.config import Config
from digitallife_v2.safety import DeviceLock
from digitallife_v2.store import LifeStore,write_json


async def probe(config,directory):
    with DeviceLock(config.serial):
        p=Path(directory).resolve();p.mkdir(parents=True,exist_ok=False)
        store=LifeStore(config.data,str(p),config.serial,'visual_capability_probe')
        code=str(secrets.randbelow(900000)+100000)
        image=Image.new('RGB',(640,360),'white');draw=ImageDraw.Draw(image)
        font=ImageFont.truetype('C:/Windows/Fonts/arial.ttf',70)
        draw.text((50,30),code,font=font,fill='black');draw.rectangle((60,170,220,310),fill='#d52222');draw.ellipse((350,170,490,310),fill='#194ddb')
        png=p/'challenge.png';image.save(png)
        frame={'observation_id':'visual-challenge','png_path':str(png),'png_sha256':hashlib.sha256(png.read_bytes()).hexdigest()}
        config.vision=True
        brain=LifeBrain(config,store)
        def validate(value):
            if not isinstance(value,dict) or set(value)!={'code','left_shape','right_shape'}:raise ValueError()
            if any(not isinstance(v,str) or len(v)>100 for v in value.values()):raise ValueError()
        try:
            value=await asyncio.wait_for(brain.structured('Read only the attached image. Return JSON with code (digits), left_shape (English color and shape), right_shape (English color and shape).',{},[frame],validate),80)
            ok=value['code']==code and 'red' in value['left_shape'].lower() and 'blue' in value['right_shape'].lower()
            proof={'source':'REAL_PROVIDER_VISUAL_CHALLENGE','passed':ok,'model':config.model,'response':value,'expected_code':code,'image_sha256':frame['png_sha256']}
            write_json(p/'VISION_READY.json',proof);store.finish('stopped' if ok else 'failed','VISION_VERIFIED' if ok else 'VISION_CHALLENGE_FAILED')
            return {'passed':ok,'model':config.model,'requests':store.state['requests']}
        finally:store.close()

def main():
    p=argparse.ArgumentParser();p.add_argument('--session',required=True);p.add_argument('--profile',default='find-x6');args=p.parse_args()
    result=asyncio.run(probe(Config.load(args.profile),args.session));print(json.dumps(result));return 0 if result['passed'] else 2
if __name__=='__main__':raise SystemExit(main())
