"""Read-only avatar image boundaries beside verified labels, not tap guesses."""
import hashlib
from pathlib import Path
from PIL import Image,ImageStat
from .safety import Blocked


def trailing_ellipsis(observation,line):
    if hashlib.sha256(Path(observation.png_path).read_bytes()).hexdigest()!=observation.png_sha256:raise Blocked('MEDIA_AVATAR_IMAGE_CHANGED')
    x1,y1,x2,y2=line.bounds;height=y2-y1
    # OCR sometimes includes all three dots in its right bound and sometimes
    # drops them. Inspect one glyph width inside and two outside that bound.
    region=(max(x1,x2-height),y1,min(observation.width,x2+max(48,height*2)),min(observation.height,y2+4))
    with Image.open(observation.png_path) as picture:
        image=picture.convert('RGB');mask={(x,y) for y in range(region[1],region[3]) for x in range(region[0],region[2]) if min(image.getpixel((x,y)))>135}
    dots=[]
    while mask:
        seed=mask.pop();queue=[seed];points=[seed]
        while queue:
            x,y=queue.pop()
            for dx,dy in ((-1,0),(1,0),(0,-1),(0,1)):
                point=(x+dx,y+dy)
                if point in mask:mask.remove(point);queue.append(point);points.append(point)
        a=min(p[0] for p in points);b=min(p[1] for p in points);c=max(p[0] for p in points)+1;d=max(p[1] for p in points)+1
        if 2<=c-a<=height*.3 and 2<=d-b<=height*.3 and (b+d)/2>y1+height*.5:dots.append((a,b,c,d))
    dots.sort()
    for a,b,c in zip(dots,dots[1:],dots[2:]):
        if (1<=b[0]-a[2]<=height*.5 and 1<=c[0]-b[2]<=height*.5
                and max(a[1],b[1],c[1])-min(a[1],b[1],c[1])<=height*.15):return True
    return False


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
