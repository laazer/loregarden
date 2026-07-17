import boxes from "../../../assets/hive/officeplace/props/boxes.png";
import chair from "../../../assets/hive/officeplace/props/chair.png";
import coffee from "../../../assets/hive/officeplace/props/coffee.png";
import couch from "../../../assets/hive/officeplace/props/couch.png";
import deskGrey from "../../../assets/hive/officeplace/props/desk-grey.png";
import deskWood from "../../../assets/hive/officeplace/props/desk-wood.png";
import fridge from "../../../assets/hive/officeplace/props/fridge.png";
import monitor from "../../../assets/hive/officeplace/props/monitor.png";
import painting from "../../../assets/hive/officeplace/props/painting.png";
import plantBig from "../../../assets/hive/officeplace/props/plant-big.png";
import plantSmall from "../../../assets/hive/officeplace/props/plant-small.png";
import shelf from "../../../assets/hive/officeplace/props/shelf.png";
import tableLong from "../../../assets/hive/officeplace/props/table-long.png";
import toilet from "../../../assets/hive/officeplace/props/toilet.png";
import vending from "../../../assets/hive/officeplace/props/vending.png";
import whiteboard from "../../../assets/hive/officeplace/props/whiteboard.png";
import windowSprite from "../../../assets/hive/officeplace/props/window.png";

/** Furniture art keyed by prop id. LimeZu office tileset — see ATTRIBUTION.md. */
export const OFFICEPLACE_PROP_URLS: Record<string, string> = {
  boxes,
  chair,
  coffee,
  couch,
  "desk-grey": deskGrey,
  "desk-wood": deskWood,
  fridge,
  monitor,
  painting,
  "plant-big": plantBig,
  "plant-small": plantSmall,
  shelf,
  "table-long": tableLong,
  toilet,
  vending,
  whiteboard,
  window: windowSprite,
};

export type OfficeplacePropId = keyof typeof OFFICEPLACE_PROP_URLS;
