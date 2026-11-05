using UnityEngine;

namespace Qiongtu.Sandbox
{
    /// <summary>The same small URP building is used on the map and in the rendered army cards.</summary>
    public static class SandboxBarracksModel
    {
        public static GameObject Build(SandboxUnitType type, Material team, Material wall, Material dark, Material metal)
        {
            var root = new GameObject("Barracks_" + type);
            Part(root, "Foundation", new Vector3(0, .2f, 0), new Vector3(17, .4f, 17), dark);
            Part(root, "RearWall", new Vector3(0, 2, -4), new Vector3(12, 4, .5f), wall);
            Part(root, "LeftWall", new Vector3(-5.8f, 2, -1), new Vector3(.5f, 4, 6), wall);
            Part(root, "RightWall", new Vector3(5.8f, 2, -1), new Vector3(.5f, 4, 6), wall);
            Part(root, "Roof", new Vector3(0, 4.3f, -1), new Vector3(12.8f, .6f, 7.5f), team);
            Part(root, "DoorLintel", new Vector3(0, 3.5f, 2.3f), new Vector3(12, 1, .6f), wall);
            Part(root, "ExitRamp", new Vector3(0, .42f, 5), new Vector3(8, .15f, 5.8f), metal);
            for (int i = -1; i <= 1; i++)
                Part(root, "RoofRib", new Vector3(i * 3.6f, 4.7f, -1), new Vector3(.15f, .15f, 7.5f), metal);
            Part(root, "FlagPole", new Vector3(-7, 4.3f, 5), new Vector3(.18f, 8, .18f), metal);
            Part(root, "TeamFlag", new Vector3(-5.6f, 7.1f, 5), new Vector3(2.8f, 1.5f, .12f), team);
            if (type == SandboxUnitType.Recon)
            {
                Part(root, "HelipadLeft", new Vector3(-1.5f, .53f, 5), new Vector3(.35f, .06f, 3), team);
                Part(root, "HelipadRight", new Vector3(1.5f, .53f, 5), new Vector3(.35f, .06f, 3), team);
                Part(root, "HelipadBridge", new Vector3(0, .53f, 5), new Vector3(3, .06f, .35f), team);
                Part(root, "Antenna", new Vector3(3.7f, 6.3f, -2), new Vector3(.2f, 3.2f, .2f), metal);
                Part(root, "AntennaCross", new Vector3(3.7f, 7.4f, -2), new Vector3(2.3f, .18f, .18f), metal);
            }
            else if (type == SandboxUnitType.Ground)
            {
                for (int i = 0; i < 3; i++) Part(root, "SupplyCrate", new Vector3(6.8f, 1 + i * .7f, -2), new Vector3(1.7f, 1.5f, 2), metal);
                Part(root, "RoofStripe", new Vector3(0, 4.65f, -1), new Vector3(3.6f, .08f, 7), dark);
            }
            else
            {
                Part(root, "ServiceCrossX", new Vector3(0, 4.65f, -1), new Vector3(5, .08f, 1.3f), metal);
                Part(root, "ServiceCrossZ", new Vector3(0, 4.66f, -1), new Vector3(1.3f, .08f, 5), metal);
                Part(root, "SupplyTank", new Vector3(6.8f, 1.4f, -1), new Vector3(2.2f, 2.8f, 4.6f), team);
            }
            root.transform.localScale = Vector3.one * SandboxArmyLayout.ModelScale;
            return root;
        }
        private static void Part(GameObject root, string name, Vector3 position, Vector3 size, Material material)
        {
            var part = GameObject.CreatePrimitive(PrimitiveType.Cube);
            part.name = name; part.transform.SetParent(root.transform, false);
            part.transform.localPosition = position; part.transform.localScale = size;
            part.GetComponent<Renderer>().sharedMaterial = material;
            var collider = part.GetComponent<Collider>();
#if UNITY_EDITOR
            if (!Application.isPlaying) Object.DestroyImmediate(collider); else Object.Destroy(collider);
#else
            Object.Destroy(collider);
#endif
        }
    }
}
