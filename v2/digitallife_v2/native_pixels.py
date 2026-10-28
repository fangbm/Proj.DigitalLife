"""Read-only avatar image boundaries beside verified labels, not tap guesses."""
import hashlib
from pathlib import Path
from PIL import Image,ImageStat
from .safety import Blocked


def avatar_bounds(observation,label,*,owner=False,sender=False):
    if hashlib.sha256(Path(observation.png_path).read_bytes()).hexdigest()!=observation.png_sha256:raise Blocked('MEDIA_AVATAR_IMAGE_CHANGED')
    w,h=observation.width,observation.height
    if sender:region=(int(w*.8),max(int(h*.05),label.bounds[1]-int(h*.3)),w,min(int(h*.94),label.bounds[3]+int(h*.06)))
    elif owner:region=(0,int(h*.05),label.bounds[0]-8,min(int(h*.35),label.bounds[3]+100))
    else:region=(0,max(int(h*.05),label.bounds[1]-int(h*.065)),label.bounds[0]-8,min(h,label.bounds[3]+int(h*.055)))
    if region[2]<=region[0] or region[3]<=region[1]:return None
    with Image.open(observation.png_path) as image:
        rgb=image.convert('RGB');crop=rgb.crop(region);edge=w-1 if sender else 0
        samples=[rgb.getpixel((edge,y)) for y in (region[1],(region[1]+region[3])//2,region[3]-1)]
    background=tuple(sorted(p[k] for p in samples)[1] for k in range(3))
    mask=set()
    for y in range(crop.height):
        for x in range(crop.width):
            r,g,b=crop.getpixel((x,y))
            # Ignore unread badges protruding beyond the actual image square.
            if r>140 and g<100 and b<100 and r>g*1.4 and r>b*1.4:continue
            if max(abs(c-background[k]) for k,c in enumerate((r,g,b)))>14:mask.add((x,y))
    candidates=[]
    while mask:
        seed=mask.pop();queue=[seed];points=[seed]
        while queue:
            x,y=queue.pop()
            for dx,dy in ((-1,-1),(-1,0),(-1,1),(0,-1),(0,1),(1,-1),(1,0),(1,1)):
                point=(x+dx,y+dy)
                if point in mask:mask.remove(point);queue.append(point);points.append(point)
        a=min(p[0] for p in points);b=min(p[1] for p in points);c=max(p[0] for p in points)+1;d=max(p[1] for p in points)+1
        width,height=c-a,d-b
        if not 45<=width<=w*.25 or not .85<width/max(1,height)<1.15 or len(points)/max(1,width*height)<.45:continue
        box=[a+region[0],b+region[1],c+region[0],d+region[1]]
        center=(box[1]+box[3])/2;label_center=(label.bounds[1]+label.bounds[3])/2
        if not owner and not sender and abs(center-label_center)>height*.9:continue
        if sender and box[1]>label.bounds[1]+height*.25:continue
        image=crop.crop((a,b,c,d));core=image.crop((width*.18,height*.18,width*.82,height*.82))
        if min(ImageStat.Stat(core).stddev)<12:continue
        candidates.append((abs(center-label_center),box))
    if owner:return candidates[0][1] if len(candidates)==1 else None
    candidates.sort(key=lambda x:x[0])
    if not candidates or len(candidates)>1 and abs(candidates[0][0]-candidates[1][0])<20:return None
    return candidates[0][1]
