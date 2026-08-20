import unittest
import xml.etree.ElementTree as ET

import trimesh

from build_webots_model import CI_OUTPUT, MAX_FACES_PER_LINK, WEBOTS_MODEL, build


class G1WebotsModelTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        build()
        cls.proto = CI_OUTPUT

    def test_proto_preserves_every_official_actuated_joint(self):
        urdf = WEBOTS_MODEL / "g1_29dof.urdf"
        official_joints = [
            joint.attrib["name"]
            for joint in ET.parse(urdf).getroot().findall("joint")
            if joint.attrib.get("type") != "fixed"
        ]
        self.assertEqual(len(official_joints), 29)
        content = self.proto.read_text(encoding="utf-8")
        for name in official_joints:
            self.assertIn(f'name "{name}"', content)
        self.assertIn("physics NULL", content)
        self.assertIn("../models/unitree_g1_webots/meshes/", content)

    def test_derived_visual_meshes_have_a_bounded_face_count(self):
        meshes = list((WEBOTS_MODEL / "meshes").glob("*.stl"))
        self.assertGreaterEqual(len(meshes), 30)
        for path in meshes:
            mesh = trimesh.load_mesh(path, process=False)
            self.assertLessEqual(len(mesh.faces), MAX_FACES_PER_LINK, path.name)


if __name__ == "__main__":
    unittest.main()
