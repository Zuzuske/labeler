import os
import re
import cv2
import numpy as np
import xml.etree.cElementTree as ET
from lxml import etree

DELAY = 20  # keyboard delay (in milliseconds)

mouse_x = 0
mouse_y = 0
point_1 = (-1, -1)
point_2 = (-1, -1)

class_index = 0
img_index = 0
img = None
img_objects = []

# selected bounding box
prev_was_double_click = False
is_bbox_selected = False
selected_bbox = -1

annotation_formats = {"YOLO_darknet": ".txt", "PASCAL_VOC": ".xml"}

INPUT_DIR = "input"
OUTPUT_DIR = "output"
WINDOW_NAME = "labeler"
TRACKBAR_IMG = "Image"
TRACKBAR_CLASS = "Class"
LINE_THICKNESS = 1


read_file = "yolo"  # pascal
drawing_mode = "drag"  # click
edit_mode = False
label_text = False
cursor_line_color = (0, 0, 255)

TRAINING_DIR = "data/train/"
TESTING_DIR = "data/test/"
training_text_file = "data/train.txt"
testing_text_file = "data/test.txt"


"""
    0,0 ------> x (width)
     |
     |  (Left,Top)
     |      *_________
     |      |         |
            |         |
     y      |_________|
  (height)            *
                (Right,Bottom)
"""


# Check if a point belongs to a rectangle
def pointInRect(pX, pY, rX_left, rY_top, rX_right, rY_bottom):
    return rX_left <= pX <= rX_right and rY_top <= pY <= rY_bottom


# Class to deal with bbox resizing
class dragBBox:
    """
        LT -- MT -- RT
        |            |
        LM          RM
        |            |
        LB -- MB -- RB
    """

    # Size of resizing anchors (depends on LINE_THICKNESS)
    sRA = LINE_THICKNESS * 1

    # Object being dragged
    selected_object = None

    # Flag indicating which resizing-anchor is dragged
    anchor_being_dragged = None

    """
    \brief This method is used to check if a current mouse position is inside one of the resizing anchors of a bbox
    """

    @staticmethod
    def check_point_inside_resizing_anchors(eX, eY, obj):
        _class_name, x_left, y_top, x_right, y_bottom = obj
        # first check if inside the bbox region (to avoid making 8 comparisons per object)
        if pointInRect(
            eX,
            eY,
            x_left - dragBBox.sRA,
            y_top - dragBBox.sRA,
            x_right + dragBBox.sRA,
            y_bottom + dragBBox.sRA,
        ):

            anchor_dict = get_anchors_rectangles(
                x_left, y_top, x_right, y_bottom)
            for anchor_key in anchor_dict:
                rX_left, rY_top, rX_right, rY_bottom = anchor_dict[anchor_key]
                if pointInRect(eX, eY, rX_left, rY_top, rX_right, rY_bottom):
                    dragBBox.anchor_being_dragged = anchor_key
                    break

    """
    \brief This method is used to select an object if one presses a resizing anchor
    """

    @staticmethod
    def handler_left_mouse_down(eX, eY, image_object):
        # Find selected_image_object_index
        if img_objects is not None:
            for idx, obj in enumerate(img_objects):
                dragBBox.check_point_inside_resizing_anchors(eX, eY, obj)
                if dragBBox.anchor_being_dragged is not None:
                    dragBBox.selected_object = obj
                    break

    @staticmethod
    def handler_mouse_move(eX, eY):
        if dragBBox.selected_object is not None:
            class_name, x_left, y_top, x_right, y_bottom = dragBBox.selected_object

            # Do not allow the bbox to flip upside down (given a margin)
            margin = 3 * dragBBox.sRA
            change_was_made = False

            if dragBBox.anchor_being_dragged[0] == "L":
                # left anchors (LT, LM, LB)
                if eX < x_right - margin:
                    x_left = eX
                    change_was_made = True

            elif dragBBox.anchor_being_dragged[0] == "R":
                # right anchors (RT, RM, RB)
                if eX > x_left + margin:
                    x_right = eX
                    change_was_made = True

            if dragBBox.anchor_being_dragged[1] == "T":
                # top anchors (LT, RT, MT)
                if eY < y_bottom - margin:
                    y_top = eY
                    change_was_made = True

            elif dragBBox.anchor_being_dragged[1] == "B":
                # bottom anchors (LB, RB, MB)
                if eY > y_top + margin:
                    y_bottom = eY
                    change_was_made = True

            if change_was_made:
                action = "resize_bbox:{}:{}:{}:{}".format(
                    x_left, y_top, x_right, y_bottom
                )

                edit_bbox(dragBBox.selected_object, action)

                # update the selected bbox
                dragBBox.selected_object = [
                    class_name,
                    x_left,
                    y_top,
                    x_right,
                    y_bottom,
                ]

    """
    This method will reset this class
    """

    @staticmethod
    def handler_left_mouse_up(eX, eY):
        if dragBBox.selected_object is not None:
            dragBBox.selected_object = None
            dragBBox.anchor_being_dragged = None


def display_text(text, time):
    #    cv2.displayOverlay(WINDOW_NAME, text, time)
    print(text)


def set_img_index(x):
    global img_index, img
    img_index = x
    img_path = IMAGE_PATH_LIST[img_index]
    img = cv2.imread(img_path)
    text = "Showing image {}/{}, path: {}".format(
        str(img_index + 1), str(last_img_index + 1), img_path
    )
    display_text(text, 2000)


def set_class_index(x):
    global class_index
    class_index = x
    text = "Selected class {}/{} -> {}".format(
        str(class_index), str(last_class_index), CLASS_LIST[class_index]
    )
    display_text(text, 2000)


def decrease_index(current_index, last_index):
    current_index -= 1
    if current_index < 0:
        current_index = last_index
    return current_index


def increase_index(current_index, last_index):
    current_index += 1
    if current_index > last_index:
        current_index = 0
    return current_index


# Draws cursor extending lines
def draw_line(img, x, y, height, width):
    cv2.line(img, (x, 0), (x, height), cursor_line_color, LINE_THICKNESS)
    cv2.line(img, (0, y), (width, y), cursor_line_color, LINE_THICKNESS)


def yolo_format(class_index, point_1, point_2, width, height):
    # YOLO wants everything normalized
    # Order: class x_center y_center x_width y_height
    x_center = (point_1[0] + point_2[0]) / float(2.0 * width)
    y_center = (point_1[1] + point_2[1]) / float(2.0 * height)
    x_width = float(abs(point_2[0] - point_1[0])) / width
    y_height = float(abs(point_2[1] - point_1[1])) / height
    items = map(str, [class_index, x_center, y_center, x_width, y_height])
    return " ".join(items)


def voc_format(class_name, point_1, point_2):
    # Order: class_name xmin ymin xmax ymax
    xmin, ymin = min(point_1[0], point_2[0]), min(point_1[1], point_2[1])
    xmax, ymax = max(point_1[0], point_2[0]), max(point_1[1], point_2[1])
    items = map(str, [class_name, xmin, ymin, xmax, ymax])
    return items


def write_xml(xml_str, xml_path):
    # remove blank text before prettifying the xml
    parser = etree.XMLParser(remove_blank_text=True)
    root = etree.fromstring(xml_str, parser)
    # prettify
    xml_str = etree.tostring(root, pretty_print=True)
    # save to file
    with open(xml_path, "wb") as temp_xml:
        temp_xml.write(xml_str)


def append_bb(ann_path, line, extension):
    if ".txt" in extension:
        with open(ann_path, "a") as myfile:
            myfile.write(line + "\n")  # append line
    elif ".xml" in extension:
        class_name, xmin, ymin, xmax, ymax = line

        tree = ET.parse(ann_path)
        annotation = tree.getroot()

        obj = ET.SubElement(annotation, "object")
        ET.SubElement(obj, "name").text = class_name
        ET.SubElement(obj, "pose").text = "Unspecified"
        ET.SubElement(obj, "truncated").text = "0"
        ET.SubElement(obj, "difficult").text = "0"

        bbox = ET.SubElement(obj, "bndbox")
        ET.SubElement(bbox, "xmin").text = xmin
        ET.SubElement(bbox, "ymin").text = ymin
        ET.SubElement(bbox, "xmax").text = xmax
        ET.SubElement(bbox, "ymax").text = ymax

        xml_str = ET.tostring(annotation)
        write_xml(xml_str, ann_path)


def yolo_to_voc(x_center, y_center, x_width, y_height, width, height):
    x_center *= float(width)
    y_center *= float(height)
    x_width *= float(width)
    y_height *= float(height)
    x_width /= 2.0
    y_height /= 2.0
    xmin = int(round(x_center - x_width))
    ymin = int(round(y_center - y_height))
    xmax = int(round(x_center + x_width))
    ymax = int(round(y_center + y_height))
    return xmin, ymin, xmax, ymax


def get_xml_object_data(data):
    class_name = data.find("name").text
    class_index = CLASS_LIST.index(class_name)
    bndbox = data.find("bndbox")
    xmin = int(bndbox.find("xmin").text)
    xmax = int(bndbox.find("xmax").text)
    ymin = int(bndbox.find("ymin").text)
    ymax = int(bndbox.find("ymax").text)
    return [class_name, class_index, xmin, ymin, xmax, ymax]


def get_txt_object_data(data, image_width, image_height):
    class_name = CLASS_LIST[int(data[0])]
    class_index = int(data[0])

    bbox_width = float(data[3]) * image_width
    bbox_height = float(data[4]) * image_height
    center_x = float(data[1]) * image_width
    center_y = float(data[2]) * image_height

    xmin = int(round(center_x - (bbox_width / 2)))
    ymin = int(round(center_y - (bbox_height / 2)))
    xmax = int(round(center_x + (bbox_width / 2)))
    ymax = int(round(center_y + (bbox_height / 2)))

    return [class_name, class_index, xmin, ymin, xmax, ymax]


def get_anchors_rectangles(xmin, ymin, xmax, ymax):
    anchor_list = {}

    mid_x = (xmin + xmax) / 2
    mid_y = (ymin + ymax) / 2

    L_ = [xmin - dragBBox.sRA, xmin + dragBBox.sRA]
    M_ = [mid_x - dragBBox.sRA, mid_x + dragBBox.sRA]
    R_ = [xmax - dragBBox.sRA, xmax + dragBBox.sRA]
    _T = [ymin - dragBBox.sRA, ymin + dragBBox.sRA]
    _M = [mid_y - dragBBox.sRA, mid_y + dragBBox.sRA]
    _B = [ymax - dragBBox.sRA, ymax + dragBBox.sRA]

    anchor_list["LT"] = [L_[0], _T[0], L_[1], _T[1]]
    anchor_list["MT"] = [M_[0], _T[0], M_[1], _T[1]]
    anchor_list["RT"] = [R_[0], _T[0], R_[1], _T[1]]
    anchor_list["LM"] = [L_[0], _M[0], L_[1], _M[1]]
    anchor_list["RM"] = [R_[0], _M[0], R_[1], _M[1]]
    anchor_list["LB"] = [L_[0], _B[0], L_[1], _B[1]]
    anchor_list["MB"] = [M_[0], _B[0], M_[1], _B[1]]
    anchor_list["RB"] = [R_[0], _B[0], R_[1], _B[1]]

    return anchor_list


def draw_bbox_anchors(tmp_img, xmin, ymin, xmax, ymax, color):
    anchor_dict = get_anchors_rectangles(xmin, ymin, xmax, ymax)
    for anchor_key in anchor_dict:
        x1, y1, x2, y2 = anchor_dict[anchor_key]
        cv2.rectangle(tmp_img, (int(x1), int(y1)),
                      (int(x2), int(y2)), color, -1)
    return tmp_img


def draw_bboxes_from_file(tmp_img, annotation_paths, width, height):
    global img_objects
    img_objects = []

    if read_file == "yolo":
        ann_path = next(
            path for path in annotation_paths if "YOLO_darknet" in path)
        if os.path.isfile(ann_path):
            with open(ann_path) as file:
                lines = list(nonblank_lines(file))
                for line in lines:
                    line = line.strip()
                    data = line.split()

                    class_name, class_index, xmin, ymin, xmax, ymax = get_txt_object_data(
                        data, width, height
                    )

                    img_objects.append([class_index, xmin, ymin, xmax, ymax])
                    color = class_rgb[class_index].tolist()

                    # draw bbox
                    cv2.rectangle(
                        tmp_img, (xmin, ymin), (xmax,
                                                ymax), color, LINE_THICKNESS
                    )

                    # draw resizing anchors
                    if edit_mode == True:
                        tmp_img = draw_bbox_anchors(
                            tmp_img, xmin, ymin, xmax, ymax, color
                        )

                    # draw labels
                    if label_text == True:
                        font = cv2.FONT_HERSHEY_SIMPLEX
                        cv2.putText(
                            tmp_img,
                            class_name,
                            (xmin, ymin - 5),
                            font,
                            0.6,
                            color,
                            LINE_THICKNESS,
                            cv2.LINE_AA,
                        )

    elif read_file == "pascal":
        ann_path = next(
            path for path in annotation_paths if "PASCAL_VOC" in path)
        if os.path.isfile(ann_path):
            tree = ET.parse(ann_path)
            annotation = tree.getroot()
            for obj in annotation.findall("object"):
                class_name, class_index, xmin, ymin, xmax, ymax = get_xml_object_data(
                    obj
                )

                img_objects.append([class_index, xmin, ymin, xmax, ymax])
                color = class_rgb[class_index].tolist()

                # draw bbox
                cv2.rectangle(
                    tmp_img, (xmin, ymin), (xmax, ymax), color, LINE_THICKNESS
                )

                # draw resizing anchors
                if edit_mode == True:
                    tmp_img = draw_bbox_anchors(
                        tmp_img, xmin, ymin, xmax, ymax, color)

                # draw labels
                if label_text == True:
                    font = cv2.FONT_HERSHEY_SIMPLEX
                    cv2.putText(
                        tmp_img,
                        class_name,
                        (xmin, ymin - 5),
                        font,
                        0.6,
                        color,
                        LINE_THICKNESS,
                        cv2.LINE_AA,
                    )
    return tmp_img


def get_bbox_area(x1, y1, x2, y2):
    width = abs(x2 - x1)
    height = abs(y2 - y1)
    return width * height


def set_selected_bbox(set_class):
    global is_bbox_selected, selected_bbox
    smallest_area = -1
    # if clicked inside multiple bboxes selects the smallest one
    for idx, obj in enumerate(img_objects):
        ind, x1, y1, x2, y2 = obj
        x1 = x1 - dragBBox.sRA
        y1 = y1 - dragBBox.sRA
        x2 = x2 + dragBBox.sRA
        y2 = y2 + dragBBox.sRA
        if pointInRect(mouse_x, mouse_y, x1, y1, x2, y2):
            is_bbox_selected = True
            tmp_area = get_bbox_area(x1, y1, x2, y2)
            if tmp_area < smallest_area or smallest_area == -1:
                smallest_area = tmp_area
                selected_bbox = idx
                if set_class:
                    # set class to the one of the selected bounding box
                    cv2.setTrackbarPos(TRACKBAR_CLASS, WINDOW_NAME, ind)


def is_mouse_inside_delete_button():
    for idx, obj in enumerate(img_objects):
        if idx == selected_bbox:
            _ind, x1, y1, x2, y2 = obj
            x1_c, y1_c, x2_c, y2_c = get_close_icon(x1, y1, x2, y2)
            if pointInRect(mouse_x, mouse_y, x1_c, y1_c, x2_c, y2_c):
                return True
    return False


def edit_bbox(obj_to_edit, action):
    """ action = `delete`
                 `change_class:new_class_index`
                 `resize_bbox:new_x_left:new_y_top:new_x_right:new_y_bottom`
    """
    if "change_class" in action:
        new_class_index = int(action.split(":")[1])
    elif "resize_bbox" in action:
        new_x_left = max(0, int(action.split(":")[1]))
        new_y_top = max(0, int(action.split(":")[2]))
        new_x_right = min(width, int(action.split(":")[3]))
        new_y_bottom = min(height, int(action.split(":")[4]))

    # 1. initialize bboxes_to_edit_dict
    #    (we use a dict since a single label can be associated with multiple ones in videos)
    bboxes_to_edit_dict = {}
    current_img_path = IMAGE_PATH_LIST[img_index]
    bboxes_to_edit_dict[current_img_path] = obj_to_edit

    # 3. loop through bboxes_to_edit_dict and edit the corresponding annotation files
    for path in bboxes_to_edit_dict:
        obj_to_edit = bboxes_to_edit_dict[path]
        class_index, xmin, ymin, xmax, ymax = map(int, obj_to_edit)

        for ann_path in get_annotation_paths(path, annotation_formats):
            if ".txt" in ann_path:
                # edit YOLO file
                with open(ann_path, "r") as old_file:
                    lines = old_file.readlines()

                yolo_line = yolo_format(
                    class_index, (xmin, ymin), (xmax, ymax), width, height
                )

                with open(ann_path, "w") as new_file:
                    for line in lines:
                        if line != yolo_line + "\n":
                            new_file.write(line)
                        elif "change_class" in action:
                            new_yolo_line = yolo_format(
                                new_class_index,
                                (xmin, ymin),
                                (xmax, ymax),
                                width,
                                height,
                            )
                            new_file.write(new_yolo_line + "\n")
                        elif "resize_bbox" in action:
                            new_yolo_line = yolo_format(
                                class_index,
                                (new_x_left, new_y_top),
                                (new_x_right, new_y_bottom),
                                width,
                                height,
                            )
                            new_file.write(new_yolo_line + "\n")
            elif ".xml" in ann_path:
                # edit PASCAL VOC file
                tree = ET.parse(ann_path)
                annotation = tree.getroot()
                for obj in annotation.findall("object"):
                    class_name_xml, class_index_xml, xmin_xml, ymin_xml, xmax_xml, ymax_xml = get_xml_object_data(
                        obj
                    )
                    if (
                        class_index == class_index_xml
                        and xmin == xmin_xml
                        and ymin == ymin_xml
                        and xmax == xmax_xml
                        and ymax == ymax_xml
                    ):
                        if "delete" in action:
                            annotation.remove(obj)
                        elif "change_class" in action:
                            # edit object class name
                            object_class = obj.find("name")
                            object_class.text = CLASS_LIST[new_class_index]
                        elif "resize_bbox" in action:
                            object_bbox = obj.find("bndbox")
                            object_bbox.find("xmin").text = str(new_x_left)
                            object_bbox.find("ymin").text = str(new_y_top)
                            object_bbox.find("xmax").text = str(new_x_right)
                            object_bbox.find("ymax").text = str(new_y_bottom)
                        break

                xml_str = ET.tostring(annotation)
                write_xml(xml_str, ann_path)


def mouse_listener(event, x, y, flags, param):
    # mouse callback function
    global is_bbox_selected, prev_was_double_click, mouse_x, mouse_y, point_1, point_2

    if event == cv2.EVENT_MOUSEMOVE:
        mouse_x = x
        mouse_y = y

        highlight_bbox()

    elif event == cv2.EVENT_LBUTTONDBLCLK:
        prev_was_double_click = True
        # print('Double click')
        point_1 = (-1, -1)
        # if clicked inside a bounding box we set that bbox
        set_class = True
        set_selected_bbox(set_class)

    elif event == cv2.EVENT_RBUTTONDOWN:
        set_class = False
        set_selected_bbox(set_class)
        if is_bbox_selected:
            obj_to_edit = img_objects[selected_bbox]
            edit_bbox(obj_to_edit, "delete")
            is_bbox_selected = False

    elif event == cv2.EVENT_LBUTTONDOWN:
        if prev_was_double_click:
            # print('Finish double click')
            prev_was_double_click = False

        # Check if mouse inside on of resizing anchors of any bboxes
        if edit_mode:
            dragBBox.handler_left_mouse_down(x, y, img_objects)

        if dragBBox.anchor_being_dragged is None:
            if point_1[0] == -1:
                if is_bbox_selected:
                    if is_mouse_inside_delete_button():
                        set_selected_bbox(set_class)
                        obj_to_edit = img_objects[selected_bbox]
                        edit_bbox(obj_to_edit, "delete")
                    is_bbox_selected = False
                else:
                    # first click (start drawing a bounding box or delete an item)
                    point_1 = (x, y)
            else:
                if drawing_mode == "click":
                    # minimal size for bounding box to avoid errors
                    threshold = 5
                    if (
                        abs(x - point_1[0]) > threshold
                        or abs(y - point_1[1]) > threshold
                    ):
                        # second click
                        point_2 = (x, y)

    elif event == cv2.EVENT_LBUTTONUP:
        if dragBBox.anchor_being_dragged is not None:
            dragBBox.handler_left_mouse_up(x, y)
        else:
            if drawing_mode == "drag":
                threshold = 5
                if abs(x - point_1[0]) > threshold or abs(y - point_1[1]) > threshold:
                    # second click
                    point_2 = (x, y)


def get_close_icon(x1, y1, x2, y2):
    percentage = 0.05
    height = -1
    while height < 15 and percentage < 1.0:
        height = int((y2 - y1) * percentage)
        percentage += 0.1
    return (x2 - height), y1, x2, (y1 + height)


def draw_close_icon(tmp_img, x1_c, y1_c, x2_c, y2_c):
    red = (0, 0, 255)
    cv2.rectangle(tmp_img, (x1_c + 1, y1_c - 1), (x2_c, y2_c), red, -1)
    white = (255, 255, 255)
    cv2.line(tmp_img, (x1_c, y1_c), (x2_c, y2_c), white, 2)
    cv2.line(tmp_img, (x1_c, y2_c), (x2_c, y1_c), white, 2)
    return tmp_img


def draw_info_bb_selected(tmp_img):
    for idx, obj in enumerate(img_objects):
        ind, x1, y1, x2, y2 = obj
        if idx == selected_bbox:
            x1_c, y1_c, x2_c, y2_c = get_close_icon(x1, y1, x2, y2)
            draw_close_icon(tmp_img, x1_c, y1_c, x2_c, y2_c)
    return tmp_img


def natural_sort_key(s, _nsre=re.compile("([0-9]+)")):
    return [int(text) if text.isdigit() else text.lower() for text in _nsre.split(s)]


def nonblank_lines(f):
    for l in f:
        line = l.rstrip()
        if line:
            yield line


def get_annotation_paths(img_path, annotation_formats):
    annotation_paths = []
    for ann_dir, ann_ext in annotation_formats.items():
        new_path = os.path.join(OUTPUT_DIR, ann_dir)
        new_path = img_path.replace(INPUT_DIR, new_path, 1)
        pre_path, img_ext = os.path.splitext(new_path)
        new_path = new_path.replace(img_ext, ann_ext, 1)
        annotation_paths.append(new_path)
    return annotation_paths


def create_PASCAL_VOC_xml(
    xml_path, abs_path, folder_name, image_name, img_height, img_width, depth
):
    # By: Jatin Kumar Mandav
    annotation = ET.Element("annotation")
    ET.SubElement(annotation, "folder").text = folder_name
    ET.SubElement(annotation, "filename").text = image_name
    ET.SubElement(annotation, "path").text = abs_path
    source = ET.SubElement(annotation, "source")
    ET.SubElement(source, "database").text = "Unknown"
    size = ET.SubElement(annotation, "size")
    ET.SubElement(size, "width").text = img_width
    ET.SubElement(size, "height").text = img_height
    ET.SubElement(size, "depth").text = depth
    ET.SubElement(annotation, "segmented").text = "0"

    xml_str = ET.tostring(annotation)
    write_xml(xml_str, xml_path)


def save_bounding_box(annotation_paths, class_index, point_1, point_2, width, height):
    for ann_path in annotation_paths:
        if ".txt" in ann_path:
            line = yolo_format(class_index, point_1, point_2, width, height)
            append_bb(ann_path, line, ".txt")
        elif ".xml" in ann_path:
            line = voc_format(CLASS_LIST[class_index], point_1, point_2)
            append_bb(ann_path, line, ".xml")


def complement_bgr(color):
    lo = min(color)
    hi = max(color)
    k = lo + hi
    return tuple(k - u for u in color)


def highlight_bbox():
    # if clicked inside multiple bboxes selects the smallest one

    for idx, obj in enumerate(img_objects):
        ind, xmin, ymin, xmax, ymax = obj

        x1 = xmin - dragBBox.sRA
        y1 = ymin - dragBBox.sRA
        x2 = xmax + dragBBox.sRA
        y2 = ymax + dragBBox.sRA

        if pointInRect(mouse_x, mouse_y, x1, y1, x2, y2):
            print("yes: " + str(obj))
            


# if imported yolo list cannot be eddited its because the data in imported file is not normalized
# this method converts/normalizes yolo txt file for current image
def convert_yolo_to_yolo_annotation_file():
    current_img_path = IMAGE_PATH_LIST[img_index]

    yolo_list = []

    for item in img_objects:
        class_index, xmin, ymin, xmax, ymax = map(int, item)
        yolo_line = yolo_format(class_index, (xmin, ymin),
                                (xmax, ymax), width, height)
        yolo_list.append(yolo_line)

    for ann_path in get_annotation_paths(current_img_path, annotation_formats):
        if ".txt" in ann_path:
            with open(ann_path, "w") as new_file:
                for line in yolo_list:
                    new_file.write(line + "\n")

                display_text("Converted " + ann_path +
                             " annotation file", 2000)


def create_directories_and_files():
    # create input dir
    if not os.path.exists(INPUT_DIR):
        os.makedirs(INPUT_DIR)

    # create output directories
    for ann_dir in annotation_formats:
        new_dir = os.path.join(OUTPUT_DIR, ann_dir)
        if not os.path.exists(new_dir):
            os.makedirs(new_dir)

    # create training dirs and files
    if not os.path.exists(TRAINING_DIR):
        os.makedirs(TRAINING_DIR)
    if not os.path.isfile(training_text_file):
        open(training_text_file, "a").close()

    # create testing dir and files
    if not os.path.exists(TESTING_DIR):
        os.makedirs(TESTING_DIR)
    if not os.path.isfile(testing_text_file):
        open(testing_text_file, "a").close()


# poop code, not dry, maybe should change, too lazy, it works
# preparing darknet data folder for training
# filling training and testing text files with file names
# these text files are used in darknet yolo training
# they tell what data/files are used for deep neural network training and testing
def populate_training_and_testing_text_files():
    files = sorted(os.listdir(TRAINING_DIR), key=natural_sort_key)
    with open(training_text_file, "w") as text_file:
        for item in files:
            if ".png" in item:
                item = os.path.join(TRAINING_DIR, item)
                text_file.write(item + "\n")
            elif ".jpg" in item:
                item = os.path.join(TRAINING_DIR, item)
                text_file.write(item + "\n")
            elif ".jpeg" in item:
                item = os.path.join(TRAINING_DIR, item)
                text_file.write(item + "\n")

    files = sorted(os.listdir(TESTING_DIR), key=natural_sort_key)
    with open(testing_text_file, "w") as text_file:
        for item in files:
            if ".png" in item:
                item = os.path.join(TESTING_DIR, item)
                text_file.write(item + "\n")
            elif ".jpg" in item:
                item = os.path.join(TESTING_DIR, item)
                text_file.write(item + "\n")
            elif ".jpeg" in item:
                item = os.path.join(TESTING_DIR, item)
                text_file.write(item + "\n")

    display_text("Creating training and testing data list", 2000)


# change to the directory of this script
os.chdir(os.path.dirname(os.path.abspath(__file__)))

if __name__ == "__main__":
    # handle directories anfd files here
    create_directories_and_files()

    # load images to list
    IMAGE_PATH_LIST = []
    files = sorted(os.listdir(INPUT_DIR), key=natural_sort_key)
    for item in files:
        file_path = os.path.join(INPUT_DIR, item)

        if os.path.isdir(file_path):  # check if it is a directory
            continue

        test_img = cv2.imread(file_path)
        if test_img is not None:  # check if file is an image
            IMAGE_PATH_LIST.append(
                file_path
            )  # if file is an imge append it to IMAGE_PATH_LIST

    # load class list to list
    with open("obj.names") as object_names:
        CLASS_LIST = list(nonblank_lines(object_names))

    last_img_index = len(IMAGE_PATH_LIST) - 1
    last_class_index = len(CLASS_LIST) - 1

    # create empty annotation files for each image, if it doesn't exist already
    for img_path in IMAGE_PATH_LIST:
        test_img = cv2.imread(img_path)
        abs_path = os.path.abspath(img_path)
        folder_name = os.path.dirname(img_path)
        image_name = os.path.basename(img_path)
        img_height, img_width, depth = (str(number)
                                        for number in test_img.shape)

        for ann_path in get_annotation_paths(img_path, annotation_formats):
            if not os.path.isfile(ann_path):
                if ".txt" in ann_path:
                    open(ann_path, "a").close()
                elif ".xml" in ann_path:
                    create_PASCAL_VOC_xml(
                        ann_path,
                        abs_path,
                        folder_name,
                        image_name,
                        img_height,
                        img_width,
                        depth,
                    )

    # Make the class colors the same each session
    # The colors are in BGR order because we're using OpenCV
    class_rgb = [(178, 103, 66)]
    class_rgb = np.array(class_rgb)
    # If there are still more classes, add new colors randomly
    num_colors_missing = len(CLASS_LIST) - len(class_rgb)
    if num_colors_missing > 0:
        more_colors = np.random.randint(
            0, 255 + 1, size=(num_colors_missing, 3))
        class_rgb = np.vstack([class_rgb, more_colors])

    # create window
    cv2.namedWindow(WINDOW_NAME, cv2.WINDOW_GUI_NORMAL)
    cv2.resizeWindow(WINDOW_NAME, 500, 500)
    cv2.setMouseCallback(WINDOW_NAME, mouse_listener)

    # selected image
    if last_img_index != -1:
        if last_img_index != 0:
            cv2.createTrackbar(
                TRACKBAR_IMG, WINDOW_NAME, 0, last_img_index + 1, set_img_index
            )

    # selected class
    if last_class_index != 0:
        cv2.createTrackbar(
            TRACKBAR_CLASS, WINDOW_NAME, 0, last_class_index, set_class_index
        )

    # welcome
    #display_text("Welcome!\n Press [h] for help.", 5000)

    # initialize
    if last_img_index != -1:
        set_img_index(0)

    # Runtime loop
    while True:
        tmp_img = None

        if last_img_index != -1:
            tmp_img = img.copy()  # clone the img
        else:
            tmp_img = 255 * np.ones(shape=[250, 250, 3], dtype=np.uint8)

        height, width = tmp_img.shape[:2]

        color = class_rgb[class_index].tolist()
        # draw vertical and horizontal guide lines
        draw_line(tmp_img, mouse_x, mouse_y, height, width)

        # write selected class
        class_name = CLASS_LIST[class_index]
        font = cv2.FONT_HERSHEY_SIMPLEX
        font_scale = 0.6
        margin = 3
        text_width, text_height = cv2.getTextSize(
            class_name, font, font_scale, LINE_THICKNESS
        )[0]

        # adds label near cursor for what class is currently active if label_text = true
        if label_text:
            tmp_img = cv2.rectangle(
                tmp_img,
                (mouse_x + LINE_THICKNESS, mouse_y - LINE_THICKNESS),
                (mouse_x + text_width + margin, mouse_y - text_height - margin),
                complement_bgr(color),
                -1,
            )
            tmp_img = cv2.putText(
                tmp_img,
                class_name,
                (mouse_x + margin, mouse_y - margin),
                font,
                font_scale,
                color,
                LINE_THICKNESS,
                cv2.LINE_AA,
            )

        if last_img_index != -1:
            # get annotation paths
            img_path = IMAGE_PATH_LIST[img_index]
            annotation_paths = get_annotation_paths(
                img_path, annotation_formats)

        if dragBBox.anchor_being_dragged is not None:
            dragBBox.handler_mouse_move(mouse_x, mouse_y)

        if last_img_index != -1:
            # draw already done bounding boxes
            tmp_img = draw_bboxes_from_file(
                tmp_img, annotation_paths, width, height)

        # if bounding box is selected add extra info
        if is_bbox_selected:
            tmp_img = draw_info_bb_selected(tmp_img)

        # if first click
        if point_1[0] != -1:
            # draw partial bbox
            cv2.rectangle(tmp_img, point_1, (mouse_x, mouse_y),
                          color, LINE_THICKNESS)
            # if second click
            if point_2[0] != -1:
                # save the bounding box
                save_bounding_box(
                    annotation_paths, class_index, point_1, point_2, width, height
                )
                # reset the points
                point_1 = (-1, -1)
                point_2 = (-1, -1)

        cv2.imshow(WINDOW_NAME, tmp_img)
        pressed_key = cv2.waitKey(DELAY)

        """ Key Listeners START """
        if dragBBox.anchor_being_dragged is None:
            if pressed_key == ord("a") or pressed_key == ord("d"):
                if pressed_key == ord("a"):
                    img_index = decrease_index(img_index, last_img_index)
                elif pressed_key == ord("d"):
                    img_index = increase_index(img_index, last_img_index)

                # not using set_img_index here bacause cv2.setTrackbarPos calls this method
                # set_img_index(img_index)
                # trackbar has onChange method, and invoking cv2.setTrackbarPos
                # changes posittion whitch in effect runs onChange method
                cv2.setTrackbarPos(TRACKBAR_IMG, WINDOW_NAME, img_index)

            elif pressed_key == ord("s") or pressed_key == ord("w"):
                if pressed_key == ord("s"):
                    class_index = decrease_index(class_index, last_class_index)
                elif pressed_key == ord("w"):
                    class_index = increase_index(class_index, last_class_index)

                # not using set_class_index here bacause cv2.setTrackbarPos calls this method
                # set_class_index(class_index)
                # trackbar has onChange method, and invoking cv2.setTrackbarPos
                # changes posittion whitch in effect runs onChange method
                cv2.setTrackbarPos(TRACKBAR_CLASS, WINDOW_NAME, class_index)

                if is_bbox_selected:
                    obj_to_edit = img_objects[selected_bbox]
                    edit_bbox(
                        obj_to_edit, "change_class:{}".format(class_index))

            elif pressed_key == ord("m"):
                if drawing_mode == "drag":
                    drawing_mode = "click"
                elif drawing_mode == "click":
                    drawing_mode = "drag"

                display_text("drawing mode switched to: " + drawing_mode, 2000)

            elif pressed_key == ord("e"):
                if edit_mode:
                    edit_mode = False
                else:
                    edit_mode = True

                display_text("edit mode enabled: " + str(edit_mode), 2000)

            elif pressed_key == ord("t"):
                if label_text:
                    label_text = False
                else:
                    label_text = True

                display_text("labels enabled: " + str(label_text), 2000)

            elif pressed_key == ord("c"):
                convert_yolo_to_yolo_annotation_file()

            elif pressed_key == ord("l"):
                populate_training_and_testing_text_files()

            elif pressed_key == ord("q"):
                cv2.destroyAllWindows()
                break

            elif pressed_key == ord("h"):
                text = (
                    "[a] or [d] to change Image\n"
                    "[w] or [s] to change Class\n"
                    "\n"
                    "[c] to re-convert uneditable yolo txt file\n"
                    "[e] to enable/disable edit mode\n"
                    "[l] to compile darknet traning data set"
                    "[m] to change drawing mode\n"
                    "[t] to enable/disable labels\n"
                    "\n"
                    "[q] to quit;\n"
                )
                display_text(text, 5000)
