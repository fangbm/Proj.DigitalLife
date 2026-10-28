"""Local screenshot geometry for Bilibili's custom-rendered controls."""
import hashlib
from pathlib import Path
from PIL import Image
from .safety import Blocked


def blue_expand_bounds(observation,line):
    if hashlib.sha256(Path(observation.png_path).read_bytes()).hexdigest()!=observation.png_sha256:raise Blocked('MEDIA_EXPAND_SOURCE_CHANGED')
    x1,y1,x2,y2=line.bounds
    if not line.text.endswith('展开'):raise Blocked('MEDIA_EXPAND_LABEL_MISSING')
    with Image.open(observation.png_path) as picture:region=picture.convert('RGB').crop(line.bounds)
    pixels=[(x,y) for y in range(region.height) for x in range(region.width) if (lambda r,g,b:b>r+30 and g>r+25)(*region.getpixel((x,y)))]
    if not pixels:raise Blocked('MEDIA_EXPAND_LINK_PIXELS_MISSING')
    a=min(x for x,y in pixels);b=min(y for x,y in pixels);c=max(x for x,y in pixels)+1;d=max(y for x,y in pixels)+1
    if not .8*(d-b)<c-a<3*(d-b) or a<region.width*.5:raise Blocked('MEDIA_EXPAND_LINK_GEOMETRY_UNVERIFIED')
    return [x1+a,y1+b,x1+c,y1+d]


def checkbox_bounds(observation,label):
    """Find the circular surface adjacent to the actual native toggle label."""
    if hashlib.sha256(Path(observation.png_path).read_bytes()).hexdigest()!=observation.png_sha256:raise Blocked('VISUAL_IMAGE_SOURCE_UNVERIFIED')
    h=label.bounds[3]-label.bounds[1]
    with Image.open(observation.png_path) as image:
        rgb=image.convert('RGB');box=(max(0,label.bounds[0]-2*h),max(0,label.bounds[1]-h//2),label.bounds[0]-3,min(rgb.height,label.bounds[3]+h//2));crop=rgb.crop(box)
    w,height=crop.size
    if w<12 or height<12:raise Blocked('MEDIA_REPOST_CHECKBOX_REGION_UNAVAILABLE')
    corners=[crop.getpixel(p) for p in ((0,0),(w-1,0),(0,height-1),(w-1,height-1))]
    background=tuple(sorted(p[k] for p in corners)[1] for k in range(3))
    mask={(x,y) for y in range(height) for x in range(w) if max(abs(v-background[k]) for k,v in enumerate(crop.getpixel((x,y))))>12}
    candidates=[]
    while mask:
        seed=mask.pop();queue=[seed];points=[seed]
        while queue:
            x,y=queue.pop()
            for dx,dy in ((-1,-1),(-1,0),(-1,1),(0,-1),(0,1),(1,-1),(1,0),(1,1)):
                point=(x+dx,y+dy)
                if point in mask:mask.remove(point);queue.append(point);points.append(point)
        a=min(x for x,y in points);b=min(y for x,y in points);c=max(x for x,y in points)+1;d=max(y for x,y in points)+1
        if (12<=c-a<=h*1.5 and .75<=(c-a)/max(1,d-b)<=1.25
                and abs((b+d)/2+box[1]-(label.bounds[1]+label.bounds[3])/2)<h*.35):
            candidates.append((a+box[0],b+box[1],c+box[0],d+box[1]))
    if len(candidates)!=1:raise Blocked('MEDIA_REPOST_CIRCLE_AMBIGUOUS_OR_MISSING')
    return candidates[0]


def empty_circle(observation,control,label):
    box=control.bounds;w,h=box[2]-box[0],box[3]-box[1];lh=label.bounds[3]-label.bounds[1]
    if (not 12<=w<=lh*1.8 or not .7<=w/max(1,h)<=1.3 or box[2]>label.bounds[0]+4
            or label.bounds[0]-box[2]>lh*1.5 or abs((box[1]+box[3]-label.bounds[1]-label.bounds[3])/2)>lh*.6):
        raise Blocked('MEDIA_REPOST_CHECKBOX_GEOMETRY_UNVERIFIED')
    if hashlib.sha256(Path(observation.png_path).read_bytes()).hexdigest()!=observation.png_sha256:raise Blocked('VISUAL_IMAGE_SOURCE_UNVERIFIED')
    with Image.open(observation.png_path) as picture:crop=picture.convert('RGB').crop(box)
    corners=[crop.getpixel((x,y)) for x,y in ((0,0),(w-1,0),(0,h-1),(w-1,h-1))]
    background=tuple(sorted(p[k] for p in corners)[1] for k in range(3));inside=[];ring=[]
    for y in range(h):
        for x in range(w):
            radius=((2*(x+.5)/w-1)**2+(2*(y+.5)/h-1)**2)**.5
            contrast=max(abs(v-background[k]) for k,v in enumerate(crop.getpixel((x,y))))
            if radius<.45:inside.append(contrast)
            elif .7<radius<1.05:ring.append(contrast)
    if not ring or sum(v>12 for v in ring)/len(ring)<.12:raise Blocked('MEDIA_REPOST_CIRCLE_NOT_OBSERVED')
    return bool(inside and sum(v>8 for v in inside)/len(inside)<=.01)
