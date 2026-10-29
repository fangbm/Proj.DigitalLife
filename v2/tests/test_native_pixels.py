import hashlib,tempfile,unittest
from pathlib import Path
from types import SimpleNamespace
from PIL import Image,ImageDraw
from digitallife_v2.native_pixels import avatar_bounds
from digitallife_v2.native_pixels import trailing_ellipsis
from digitallife_v2.safety import Blocked


class AvatarBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.path=Path(self.tmp.name)/'frame.png'
    def frame(self,box=(30,300,130,400),blank=False):
        image=Image.new('RGB',(600,1200),(20,20,20));draw=ImageDraw.Draw(image)
        if not blank:
            x,y,r,b=box;draw.rectangle((x,y,r-1,b-1),fill=(80,90,100))
            draw.rectangle((x+10,y+10,x+49,y+79),fill=(220,190,60))
            draw.rectangle((x+50,y+10,r-11,y+79),fill=(40,90,220))
        image.save(self.path)
        return SimpleNamespace(png_path=str(self.path),png_sha256=hashlib.sha256(self.path.read_bytes()).hexdigest(),width=600,height=1200)
    def test_square_pixels_beside_verified_label_are_returned_exactly(self):
        obs=self.frame();label=SimpleNamespace(bounds=(170,330,300,365))
        self.assertEqual(avatar_bounds(obs,label),[30,300,130,400])
    def test_sender_pixels_are_scoped_to_right_side(self):
        obs=self.frame((500,300,590,390));label=SimpleNamespace(bounds=(180,350,400,380))
        self.assertEqual(avatar_bounds(obs,label,sender=True),[500,300,590,390])
    def test_blank_or_tampered_frame_cannot_locate_an_avatar(self):
        obs=self.frame(blank=True);label=SimpleNamespace(bounds=(170,330,300,365))
        self.assertIsNone(avatar_bounds(obs,label));self.path.write_bytes(b'changed')
        with self.assertRaises(Blocked):avatar_bounds(obs,label)

    def test_trailing_ellipsis_requires_three_actual_baseline_dots(self):
        obs=self.frame(blank=True);line=SimpleNamespace(bounds=(100,100,200,140))
        with Image.open(self.path) as image:
            draw=ImageDraw.Draw(image)
            for x in (194,202):draw.rectangle((x,130,x+2,132),fill=(210,210,210))
            image.save(self.path)
        obs.png_sha256=hashlib.sha256(self.path.read_bytes()).hexdigest();self.assertFalse(trailing_ellipsis(obs,line))
        with Image.open(self.path) as image:ImageDraw.Draw(image).rectangle((210,130,212,132),fill=(210,210,210));image.save(self.path)
        obs.png_sha256=hashlib.sha256(self.path.read_bytes()).hexdigest();self.assertTrue(trailing_ellipsis(obs,line))
        line.bounds=(100,100,214,140)
        self.assertTrue(trailing_ellipsis(obs,line))
