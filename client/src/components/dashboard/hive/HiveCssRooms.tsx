import type { CSSProperties } from "react";

import type { FloorDesk, FloorDoor, FloorProp, FloorRoom } from "../../../lib/hive/layouts";
import { OFFICEPLACE_PROP_URLS } from "../../../lib/hive/layouts/officeplaceProps";

interface HiveCssRoomsProps {
  rooms: FloorRoom[];
  desks: FloorDesk[];
  props: FloorProp[];
  doors: FloorDoor[];
  map: { width: number; height: number };
}

type Rect = { x: number; y: number; w: number; h: number };
type Map2D = { width: number; height: number };

function rectStyle(rect: Rect, map: Map2D): CSSProperties {
  return {
    left: `${(rect.x / map.width) * 100}%`,
    top: `${(rect.y / map.height) * 100}%`,
    width: `${(rect.w / map.width) * 100}%`,
    height: `${(rect.h / map.height) * 100}%`,
  };
}

/**
 * A rotated sprite is laid out at its pre-rotation size and spun about the rect's
 * centre, so its rotated bounding box lands exactly on the rect. Tiles are square
 * (the floor letterboxes to the map aspect), so swapping tile counts across axes
 * is sound even though the percentages resolve against different bases.
 */
function propStyle(prop: FloorProp, map: Map2D): CSSProperties {
  const rotate = prop.rotate ?? 0;
  if (!rotate) return rectStyle(prop, map);
  const swap = rotate === 90 || rotate === 270;
  return {
    left: `${((prop.x + prop.w / 2) / map.width) * 100}%`,
    top: `${((prop.y + prop.h / 2) / map.height) * 100}%`,
    width: `${((swap ? prop.h : prop.w) / map.width) * 100}%`,
    height: `${((swap ? prop.w : prop.h) / map.height) * 100}%`,
    transform: `translate(-50%, -50%) rotate(${rotate}deg)`,
  };
}

/**
 * Draws the floor plan — walled rooms, doorways, desks and furniture — from layout
 * data. Replaces the baked scenery bitmap for the officeplace skin.
 */
export function HiveCssRooms({ rooms, desks, props, doors, map }: HiveCssRoomsProps) {
  if (!rooms.length) return null;

  return (
    <div className="hive-css__rooms" aria-hidden>
      {rooms.map((room) => (
        <div
          key={room.id}
          className={`hive-css__room hive-css__room--${room.kind}`}
          data-testid={`hive-room-${room.id}`}
          style={rectStyle(room, map)}
        >
          <span className="hive-css__room-name">{room.label}</span>
        </div>
      ))}

      {/* Punched over the wall band so a doorway reads as a gap, not a hole. */}
      {doors.map((door, i) => (
        <div
          key={`door-${door.x}-${door.y}-${i}`}
          className="hive-css__door"
          data-testid="hive-door"
          style={rectStyle(door, map)}
        />
      ))}

      {/* Wrap desks are drawn by their counter run in props; the rect is collision only. */}
      {desks.filter((desk) => !desk.wrap).map((desk) => {
        const art = OFFICEPLACE_PROP_URLS[desk.sprite ?? "desk-wood"];
        if (!art) return null;
        return (
          <img
            key={desk.id}
            className="hive-css__prop"
            data-testid={`hive-desk-${desk.id}`}
            src={art}
            alt=""
            draggable={false}
            title={desk.label}
            style={rectStyle(desk, map)}
          />
        );
      })}

      {props.map((prop) => {
        const art = OFFICEPLACE_PROP_URLS[prop.sprite];
        if (!art) return null;
        return (
          <img
            key={prop.id}
            className="hive-css__prop"
            data-testid={`hive-prop-${prop.id}`}
            src={art}
            alt=""
            draggable={false}
            style={propStyle(prop, map)}
          />
        );
      })}
    </div>
  );
}
