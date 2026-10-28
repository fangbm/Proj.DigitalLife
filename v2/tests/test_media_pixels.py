import hashlib,tempfile,unittest
from pathlib import Path
from types import SimpleNamespace
from PIL import Image,ImageDraw
from digitallife_v2.media_pixels import empty_circle,checkbox_bounds,blue_expand_bounds
from digitallife_v2.safety import Blocked


class RepostCheckboxTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.path=Path(self.tmp.name)/'fixture.png'
        self.control=SimpleNamespace(bounds=(40,40,76,76));self.label=SimpleNamespace(bounds=(90,40,200,76))
    def frame(self,selected=False,ring=True):
        image=Image.new('RGB',(220,150),(36,36,36));draw=ImageDraw.Draw(image)
        if ring:draw.ellipse((40,40,75,75),outline=(100,100,100),width=3,fill=(190,50,100) if selected else None)
        image.save(self.path)
        return SimpleNamespace(png_path=str(self.path),png_sha256=hashlib.sha256(self.path.read_bytes()).hexdigest())
    def test_actual_empty_circle_is_off(self):self.assertTrue(empty_circle(self.frame(),self.control,self.label))
    def test_native_label_anchors_the_actual_circle_instead_of_model_coordinates(self):
        self.assertEqual(checkbox_bounds(self.frame(),self.label),self.control.bounds)
    def test_selected_filled_circle_is_not_off(self):self.assertFalse(empty_circle(self.frame(True),self.control,self.label))
    def test_blank_background_cannot_masquerade_as_an_unchecked_control(self):
        with self.assertRaisesRegex(Blocked,'NOT_OBSERVED'):empty_circle(self.frame(ring=False),self.control,self.label)
    def test_source_or_geometry_changes_are_rejected(self):
        frame=self.frame();self.path.write_bytes(b'changed')
        with self.assertRaises(Blocked):empty_circle(frame,self.control,self.label)
        with self.assertRaises(Blocked):empty_circle(self.frame(),SimpleNamespace(bounds=(0,0,200,120)),self.label)

    def test_expand_target_comes_from_actual_blue_pixels_inside_OCR_line(self):
        self.frame();image=Image.open(self.path);draw=ImageDraw.Draw(image);draw.rectangle((150,45,189,64),fill=(30,160,190));image.save(self.path);image.close()
        frame=SimpleNamespace(png_path=str(self.path),png_sha256=hashlib.sha256(self.path.read_bytes()).hexdigest())
        self.assertEqual(blue_expand_bounds(frame,SimpleNamespace(text='正文…展开',bounds=(20,40,200,76))),[150,45,190,65])
        self.path.write_bytes(b'changed')
        with self.assertRaises(Blocked):blue_expand_bounds(frame,SimpleNamespace(text='正文…展开',bounds=(20,40,200,76)))

    def test_no_blue_link_means_no_expand_tap(self):
        with self.assertRaises(Blocked):blue_expand_bounds(self.frame(),SimpleNamespace(text='正文…展开',bounds=(20,40,200,76)))
