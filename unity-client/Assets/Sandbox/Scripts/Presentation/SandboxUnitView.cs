using System.Collections.Generic;
using UnityEngine;

namespace Qiongtu.Sandbox
{
    /// <summary>Selectable authored model with terrain following, heading, rotors, wheels, and arrival feedback.</summary>
    public sealed class SandboxUnitView : MonoBehaviour
    {
        public const float ModelScale = 2f;
        public const float ReconAltitude = 6f * 8f;
        public const float GroundClearance = .08f;
        public Bounds VisualBounds => visualBounds;
        public bool IsAirborne => type == SandboxUnitType.Recon;
        public Vector3 MapLabelPosition => new Vector3(transform.position.x, visualBounds.max.y + .8f, transform.position.z);
        private Renderer[] renderers;
        private BoxCollider pickCollider;
        private Bounds visualBounds;
        public string UnitId { get; private set; }
        public bool IsMoving => path.Count > 0;
        private SandboxUnitType type;
        private Transform visual;
        private readonly List<Transform> rotors = new List<Transform>();
        private readonly List<Transform> wheels = new List<Transform>();
        private readonly Queue<Vector3> path = new Queue<Vector3>();
        private Vector3 destination;
        private bool initialized;
        private float phase;
        private float speed;
        private MaterialPropertyBlock block;
        private LineRenderer route;
        private float spawnAt;
        private static readonly int BaseColor = Shader.PropertyToID("_BaseColor");

        public void Initialize(string unitId, SandboxUnitType unitType, Transform model, Color sideColor)
        {
            UnitId = unitId; type = unitType; visual = model;
            visual.localScale = Vector3.one * ModelScale;
            renderers = visual.GetComponentsInChildren<Renderer>();
            pickCollider = GetComponent<BoxCollider>();
            phase = (unitId.GetHashCode() & 65535) * 0.03f; spawnAt = Time.time;
            foreach (Transform part in visual.GetComponentsInChildren<Transform>())
            {
                if (part.name.StartsWith("RotorPivot_")) rotors.Add(part);
                if (part.name.StartsWith("WheelPivot_")) wheels.Add(part);
            }
            block = new MaterialPropertyBlock(); block.SetColor(BaseColor, sideColor);
            foreach (Renderer renderer in visual.GetComponentsInChildren<Renderer>())
                if (renderer.sharedMaterial.name.StartsWith("TeamAccent")) renderer.SetPropertyBlock(block);
        }

        public bool HasPosition => initialized;
        public bool HasTarget(Vector3 next) => initialized && (destination - next).sqrMagnitude < 0.01f;

        public void SetTarget(Vector3 next, List<Vector3> waypoints = null, bool force = false)
        {
            if (!initialized)
            {
                destination = next; transform.position = next; initialized = true;
                transform.rotation = Quaternion.Euler(0, UnitId.StartsWith("R") ? 75 : -75, 0);
                UpdateVisualPose(Time.time, false, 1); return;
            }
            if (!force && (destination - next).sqrMagnitude < 0.01f) return;
            destination = next; path.Clear();
            if (waypoints != null) foreach (Vector3 point in waypoints) path.Enqueue(point);
            if (path.Count == 0) path.Enqueue(next);
        }

        public void Relocate(Vector3 position)
        {
            path.Clear(); destination = position; transform.position = position; speed = 0; initialized = true; spawnAt = Time.time;
            UpdateVisualPose(Time.time, false, 1);
        }

        public void HoldPosition() { path.Clear(); }

        public void SpawnAt(Vector3 origin, float delay)
        {
            Relocate(origin); spawnAt = Time.time + delay; visual.localScale = Vector3.zero;
            if (pickCollider != null) pickCollider.enabled = false;
        }

        private void OnDestroy() => ReleaseRoute();

        public void ReleaseRoute()
        {
            if (route == null) return;
            GameObject line = route.gameObject;
            route = null;
            line.SetActive(false);
#if UNITY_EDITOR
            if (!Application.isPlaying) { DestroyImmediate(line); return; }
#endif
            Destroy(line);
        }

        public void AttachRoute(LineRenderer line) => route = line;
        public void HideRoute() { if (route != null) route.gameObject.SetActive(false); }
        public void ShowRoute(bool selected) { if (route != null) route.gameObject.SetActive(selected && IsMoving); }

        private void Update()
        {
            if (Time.time < spawnAt) return;
            float dt = Time.deltaTime;
            bool moving = path.Count > 0;
            speed = Mathf.MoveTowards(speed, moving ? (type == SandboxUnitType.Recon ? 15 : type == SandboxUnitType.Ground ? 8 : 6) : 0, dt * 12);
            float traveled = 0;
            if (moving)
            {
                Vector3 target = path.Peek();
                Vector3 flat = target - transform.position; flat.y = 0;
                if (flat.sqrMagnitude > 0.02f)
                {
                    Quaternion heading = Quaternion.LookRotation(flat);
                    transform.rotation = Quaternion.RotateTowards(transform.rotation, heading, (type == SandboxUnitType.Recon ? 200 : 110) * dt);
                }
                Vector3 before = transform.position;
                transform.position = Vector3.MoveTowards(before, new Vector3(target.x, before.y, target.z), speed * dt);
                // Interpolate only XZ; sample terrain/bridge each frame rather than cutting through sloping ground.
                Vector3 position = transform.position;
                traveled = Vector2.Distance(new Vector2(before.x, before.z), new Vector2(position.x, position.z));
                if (Vector2.Distance(new Vector2(target.x, target.z), new Vector2(position.x, position.z)) < 0.18f) path.Dequeue();
            }
            // Idle, restored and moving units all use the same surface-relative altitude.
            Vector3 anchored = transform.position;
            anchored.y = SandboxWorld.Surface(anchored.x, anchored.z).y + (type == SandboxUnitType.Recon ? ReconAltitude : GroundClearance);
            transform.position = anchored;
            foreach (Transform rotor in rotors) rotor.Rotate(Vector3.up, dt * (moving ? 2400 : 1700), Space.Self);
            foreach (Transform wheel in wheels) wheel.Rotate(Vector3.right, traveled * Mathf.Rad2Deg / (0.4f * ModelScale), Space.Self);
            float spawn = Mathf.Clamp01((Time.time - spawnAt) / 0.35f);
            UpdateVisualPose(Time.time, moving, spawn);
            if (route != null && route.gameObject.activeSelf)
            {
                route.positionCount = path.Count + 1; route.SetPosition(0, transform.position + Vector3.up * 0.2f);
                int i = 1; foreach (Vector3 point in path) route.SetPosition(i++, point + Vector3.up * 0.2f);
                if (!IsMoving) route.gameObject.SetActive(false);
            }
        }

        /// <summary>Keep the doubled animated mesh and its pick volume clear of terrain, including banked flight and slopes.</summary>
        private void UpdateVisualPose(float time, bool moving, float spawn)
        {
            visual.localScale = Vector3.one * ModelScale * Mathf.Lerp(.2f, 1, 1 - Mathf.Pow(1 - Mathf.Clamp01(spawn), 3));
            visual.localPosition = type == SandboxUnitType.Recon ? Vector3.up * Mathf.Sin(time * 2.4f + phase) * .16f
                : Vector3.up * Mathf.Sin(time * 22 + phase) * Mathf.Min(speed, 1) * .025f;
            visual.localRotation = type == SandboxUnitType.Recon ? Quaternion.Euler(moving ? -5 : Mathf.Sin(time + phase) * 1.3f, 0, Mathf.Sin(time * 1.8f + phase) * 1.4f) : Quaternion.identity;
            visualBounds = renderers[0].bounds;
            for (int i = 1; i < renderers.Length; i++) visualBounds.Encapsulate(renderers[i].bounds);
            // Sample the whole enlarged footprint, not only the model's centre. Lift the visual,
            // not the authoritative XZ/target/root, so restoring or refreshing cannot accumulate offsets.
            float surface = SandboxWorld.HighestSurface(visualBounds);
            float lift = Mathf.Max(0, surface + (type == SandboxUnitType.Recon ? ReconAltitude : GroundClearance) - visualBounds.min.y);
            visual.localPosition += Vector3.up * lift;
            visualBounds.center += Vector3.up * lift;
            if (pickCollider == null) return;
            // Convert the animated world bounds to root space; the collider follows scale, bob and lift.
            Matrix4x4 inverse = transform.worldToLocalMatrix;
            Vector3 a = inverse.MultiplyVector(Vector3.right * visualBounds.extents.x);
            Vector3 b = inverse.MultiplyVector(Vector3.up * visualBounds.extents.y);
            Vector3 c = inverse.MultiplyVector(Vector3.forward * visualBounds.extents.z);
            pickCollider.center = inverse.MultiplyPoint3x4(visualBounds.center);
            pickCollider.size = Vector3.Max(new Vector3(Mathf.Abs(a.x) + Mathf.Abs(b.x) + Mathf.Abs(c.x),
                Mathf.Abs(a.y) + Mathf.Abs(b.y) + Mathf.Abs(c.y), Mathf.Abs(a.z) + Mathf.Abs(b.z) + Mathf.Abs(c.z)) * 2,
                new Vector3(3, 1.8f, 3) * ModelScale);
            pickCollider.enabled = true;
        }
    }
}
