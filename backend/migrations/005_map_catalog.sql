-- 仅约束新增地图格式；旧场景内容和既有二十表保持兼容。
SET LOCAL search_path = sandbox, pg_catalog;

CREATE FUNCTION validate_map_catalog() RETURNS trigger LANGUAGE plpgsql
SET search_path = pg_catalog, sandbox AS $function$
DECLARE
    map_data jsonb := NEW.definition->'map';
    grid jsonb;
    columns_count integer;
    rows_count integer;
BEGIN
    IF map_data->>'schema_version' IS DISTINCT FROM 'map-elevation-roads-v2' THEN
        RETURN NEW;
    END IF;
    grid := map_data->'elevation_grid';
    IF jsonb_typeof(grid) IS DISTINCT FROM 'object'
       OR jsonb_typeof(grid->'values') IS DISTINCT FROM 'array'
       OR jsonb_typeof(map_data->'road_nodes') IS DISTINCT FROM 'array'
       OR jsonb_typeof(map_data->'road_edges') IS DISTINCT FROM 'array'
       OR jsonb_typeof(map_data->'bridges') IS DISTINCT FROM 'array'
       OR length(COALESCE(map_data->>'map_id','')) = 0
       OR length(COALESCE(map_data->>'map_version','')) = 0
       OR COALESCE(map_data->>'content_sha256','') !~ '^[0-9a-f]{64}$' THEN
        RAISE EXCEPTION '地图高程或道路结构缺失' USING ERRCODE = '23514';
    END IF;
    IF jsonb_typeof(grid->'columns') IS DISTINCT FROM 'number'
       OR jsonb_typeof(grid->'rows') IS DISTINCT FROM 'number'
       OR grid->>'columns' !~ '^[0-9]+$' OR grid->>'rows' !~ '^[0-9]+$' THEN
        RAISE EXCEPTION '地图高程行列必须为整数' USING ERRCODE = '23514';
    END IF;
    columns_count := (grid->>'columns')::integer;
    rows_count := (grid->>'rows')::integer;
    IF columns_count < 2 OR rows_count < 2
       OR jsonb_array_length(grid->'values')::bigint <> columns_count::bigint * rows_count::bigint
       OR EXISTS (SELECT 1 FROM jsonb_array_elements(grid->'values') AS sample
                  WHERE jsonb_typeof(sample) <> 'number') THEN
        RAISE EXCEPTION '地图高程数量或数值类型无效' USING ERRCODE = '23514';
    END IF;
    IF EXISTS (SELECT 1 FROM jsonb_array_elements(map_data->'road_nodes') AS node
               WHERE length(COALESCE(node->>'node_id','')) = 0)
       OR EXISTS (SELECT 1 FROM jsonb_array_elements(map_data->'road_nodes') AS node
                  GROUP BY node->>'node_id' HAVING count(*) > 1)
       OR EXISTS (SELECT 1 FROM jsonb_array_elements(map_data->'road_edges') AS edge
                  GROUP BY edge->>'edge_id' HAVING count(*) > 1) THEN
        RAISE EXCEPTION '地图节点或道路身份缺失、重复' USING ERRCODE = '23514';
    END IF;
    IF EXISTS (
        SELECT 1 FROM jsonb_array_elements(map_data->'road_edges') AS edge
        WHERE length(COALESCE(edge->>'edge_id','')) = 0
           OR edge->>'from_node_id' IS NOT DISTINCT FROM edge->>'to_node_id'
           OR NOT EXISTS (SELECT 1 FROM jsonb_array_elements(map_data->'road_nodes') AS node
                          WHERE node->>'node_id' = edge->>'from_node_id')
           OR NOT EXISTS (SELECT 1 FROM jsonb_array_elements(map_data->'road_nodes') AS node
                          WHERE node->>'node_id' = edge->>'to_node_id')
    ) THEN
        RAISE EXCEPTION '地图道路端点引用无效' USING ERRCODE = '23514';
    END IF;
    RETURN NEW;
END;
$function$;
CREATE TRIGGER scenario_map_catalog_guard BEFORE INSERT ON scenario_versions
    FOR EACH ROW EXECUTE FUNCTION validate_map_catalog();
REVOKE ALL ON FUNCTION validate_map_catalog() FROM PUBLIC;

CREATE INDEX scenario_map_catalog_idx ON scenario_versions
    ((definition #>> '{map,map_id}'), (definition #>> '{map,map_version}'))
    WHERE definition #>> '{map,schema_version}' = 'map-elevation-roads-v2';

COMMENT ON INDEX scenario_map_catalog_idx IS '新版地图目录查询；不为完整高度数组建立通用 GIN';
