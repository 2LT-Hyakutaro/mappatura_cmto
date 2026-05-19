"""
QGIS Processing Algorithm – cmto_mappatura_algo
==========================================

L'algoritmo, da eseguire sul layer 'strade provinciali', prende come parametro in ingresso il nome di una strada (formato 'pXXX') 
e produce una lista di coordinate di punti posti a intervalli fissi lungo la strada.
"""

from qgis.core import (
    QgsProcessingAlgorithm,
    QgsProcessingParameterString,
    QgsProcessingParameterFileDestination,
    QgsProcessingException,
    QgsCoordinateReferenceSystem,
    QgsCoordinateTransform,
    QgsPointXY,
    QgsProject,
)
import math
import json


# ── Configuration ────────────────────────────────────────────────────────────
LAYER_NAME  = 'Strade provinciali'       # Name of the vector layer in the QGIS project
NAME_FIELD  = 'elemento'                 # Attribute field that holds each feature's name
STRIDE_M    = 20.0                       # Sampling interval in metres
SRC_CRS     = 'EPSG:32632'               # CRS of the layer
DST_CRS     = 'EPSG:4326'                # Target CRS for output (WGS 84)
# ─────────────────────────────────────────────────────────────────────────────


class StradeSampler(QgsProcessingAlgorithm):

    # Parameter keys
    PARAM_ROAD   = 'ROAD'
    PARAM_OUTPUT = 'OUTPUT'

    # ── Algorithm metadata ────────────────────────────────────────────────────

    def name(self):
        return 'cmto_mappatura_algo'

    def displayName(self):
        return 'Mappatura automatica elementi sicurezza CMTO'

    def group(self):
        return 'Custom'

    def groupId(self):
        return 'custom'

    def shortHelpString(self):
        return (
            '''L'algoritmo, da eseguire sul layer 'strade provinciali', prende come parametro in ingresso il nome di una strada (formato 'pXXX') 
e produce una lista di coordinate di punti posti a intervalli fissi lungo la strada.\n\n
            Per ogni punto nella lista si ottengono:\n
              • Distanza dall'origine (m)\n
              • Latitude and Longitude (WGS 84)\n"
              • Orientamento verso il punto successivo (°)\n\n
            Results are saved as a JSON file.'''
        )

    def createInstance(self):
        return StradeSampler()

    # ── Parameters ────────────────────────────────────────────────────────────

    def initAlgorithm(self, config=None):
        self.addParameter(
            QgsProcessingParameterString(
                self.PARAM_NAME,
                'Strada su cui effettuare l\' analisi (corrispondente al campo "{}" del layer Strade provinciale)'.format(NAME_FIELD),
            )
        )

        self.addParameter(
            QgsProcessingParameterFileDestination(
                self.PARAM_OUTPUT,
                'File di output',
                fileFilter='JSON files (*.json)',
            )
        )

    # ── Main processing ───────────────────────────────────────────────────────

    def processAlgorithm(self, parameters, context, feedback):

        # 1. Read input parameters
        target_road = self.parameterAsString(parameters, self.PARAM_ROAD, context).strip()
        output_path = self.parameterAsFileOutput(parameters, self.PARAM_OUTPUT, context)

        if not target_road:
            raise QgsProcessingException("The 'name' parameter cannot be empty.")

        feedback.pushInfo(f"Looking for feature with {NAME_FIELD} = '{target_road}' ...")

        # 2. Locate the 'strade' layer
        layers = QgsProject.instance().mapLayersByName(LAYER_NAME)
        if not layers:
            raise QgsProcessingException(
                f"Layer '{LAYER_NAME}' was not found in the current project."
            )
        layer = layers[0]

        # 3. Find the feature whose NAME_FIELD matches target_name
        feature = None
        for feat in layer.getFeatures():
            if str(feat[NAME_FIELD]).strip() == target_road:
                feature = feat
                break

        if feature is None:
            raise QgsProcessingException(
                f"No feature with {NAME_FIELD} = '{target_road}' was found in '{LAYER_NAME}'."
            )

        # 4. Set up coordinate transform (EPSG:32632 → EPSG:4326)
        crs_src   = QgsCoordinateReferenceSystem(SRC_CRS)
        crs_dst   = QgsCoordinateReferenceSystem(DST_CRS)
        transform = QgsCoordinateTransform(crs_src, crs_dst, QgsProject.instance())

        # 5. Sample the line
        geometry     = feature.geometry()
        total_length = geometry.length()

        feedback.pushInfo(
            f"Feature found. Total length: {total_length:.1f} m. "
            f"Sampling every {STRIDE_M} m ..."
        )

        # Build list of (x, y) in EPSG:32632 at each sample distance
        raw_points = []
        distance   = 0.0
        while distance < total_length:
            pt = geometry.interpolate(distance).asPoint()
            raw_points.append((pt.x(), pt.y(), distance))
            distance += STRIDE_M

        # Always include the exact end of the line
        end_pt = geometry.interpolate(total_length).asPoint()
        raw_points.append((end_pt.x(), end_pt.y(), total_length))

        # 6. Compute headings and convert coordinates
        sample_points = []
        n = len(raw_points)

        for i, (x, y, dist) in enumerate(raw_points):
            if feedback.isCanceled():
                break

            # Convert projected coordinates to lat/lon
            pt_wgs84  = transform.transform(QgsPointXY(x, y))
            longitude = pt_wgs84.x()
            latitude  = pt_wgs84.y()

            # Heading: bearing from this point toward the next one
            if i < n - 1:
                nx, ny, _ = raw_points[i + 1]
                dx        = nx - x               # Easting  difference
                dy        = ny - y               # Northing difference
                # atan2(dx, dy): angle from North, clockwise → compass bearing
                heading   = math.degrees(math.atan2(dx, dy)) % 360
            else:
                # Last point: carry forward the previous heading
                heading = sample_points[-1]['heading_deg'] if sample_points else 0.0

            sample_points.append({
                'distance_m':   round(dist, 3),
                'latitude':     round(latitude,  8),
                'longitude':    round(longitude, 8),
                'heading_deg':  round(heading,   4),
            })

            # Report progress every 50 points
            if i % 50 == 0:
                feedback.setProgress(int(100 * i / n))

        # 7. Write JSON output
        output_data = {
            'feature_name': target_road,
            'layer':        LAYER_NAME,
            'crs_source':   SRC_CRS,
            'crs_output':   DST_CRS,
            'stride_m':     STRIDE_M,
            'total_length_m': round(total_length, 3),
            'point_count':  len(sample_points),
            'points':       sample_points,
        }

        with open(output_path, 'w', encoding='utf-8') as f:
            json.dump(output_data, f, indent=2, ensure_ascii=False)

        feedback.setProgress(100)
        feedback.pushInfo(
            f"Done. {len(sample_points)} sample points saved to:\n{output_path}"
        )

        return {self.PARAM_OUTPUT: output_path}
