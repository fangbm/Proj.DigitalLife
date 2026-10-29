import hashlib,tempfile,unittest,xml.etree.ElementTree as ET
from pathlib import Path
from digitallife_v2.bilibili import BilibiliUI
from digitallife_v2.media import BILIBILI
from digitallife_v2.perception import parse_xml
from digitallife_v2.safety import Blocked
from test_m1 import observation


class ProfileUIDTests(unittest.TestCase):
    def profile(self,folder,uid=None,second_uid=None):
        root=ET.Element('hierarchy')
        def node(parent,text,rid='',clickable=False):
            return ET.SubElement(parent,'node',{'text':text,'resource-id':BILIBILI+':id/'+rid if rid else '',
                'package':BILIBILI,'bounds':'[20,200][500,250]','class':'android.widget.TextView','clickable':str(clickable).lower(),'enabled':'true'})
        node(root,'Fixture public sender','header_info_name');node(root,'567890')
        node(root,'123456','fans')
        if uid:
            tags=node(root,'','bottom_tags_layout');node(tags,uid,clickable=True)
            if second_uid:node(tags,second_uid,clickable=True)
        path=Path(folder)/'profile.xml';path.write_bytes(ET.tostring(root,encoding='utf-8',xml_declaration=True))
        obs=observation('profile',foreground=BILIBILI+'/.AuthorSpaceActivity');obs.xml_path=str(path);obs.xml_sha256=hashlib.sha256(path.read_bytes()).hexdigest()
        _,obs.controls,_,_=parse_xml(path.read_text(encoding='utf-8'),obs.observation_id,full_access=True);return obs
    def test_badge_and_follower_numbers_cannot_substitute_for_a_missing_uid(self):
        with tempfile.TemporaryDirectory() as folder:
            with self.assertRaisesRegex(Blocked,'PROFILE_UID_UNAVAILABLE'):BilibiliUI(None).profile_uid(self.profile(folder))
    def test_expanded_native_uid_is_selected_even_with_an_unrelated_badge_number(self):
        with tempfile.TemporaryDirectory() as folder:
            self.assertEqual(BilibiliUI(None).profile_uid(self.profile(folder,'87654321')),('87654321','Fixture public sender'))
    def test_ambiguous_or_modified_native_uid_evidence_is_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            with self.assertRaises(Blocked):BilibiliUI(None).profile_uid(self.profile(folder,'87654321','98765432'))
            obs=self.profile(folder,'87654321');Path(obs.xml_path).write_bytes(b'<hierarchy/>')
            with self.assertRaises(Blocked):BilibiliUI(None).profile_uid(obs)
