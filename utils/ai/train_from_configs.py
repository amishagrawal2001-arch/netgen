#!/usr/bin/env python3
"""
Script to train AI models from network device configurations
Usage: python train_from_configs.py [options]
"""

import argparse
import sys
import logging
from pathlib import Path
from typing import Dict

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def main():
    parser = argparse.ArgumentParser(description="Train AI models from device configurations")
    parser.add_argument("--import-ostg", action="store_true", 
                       help="Import all devices from OSTG database")
    parser.add_argument("--config-dir", type=str,
                       help="Directory containing configuration files")
    parser.add_argument("--config-file", type=str,
                       help="Single configuration file to import")
    parser.add_argument("--vendor", type=str, choices=["juniper", "cisco", "frr", "auto"],
                       default="auto", help="Vendor type (auto-detect if not specified)")
    parser.add_argument("--device-id", type=str,
                       help="Device ID for single config import")
    parser.add_argument("--device-name", type=str,
                       help="Device name for single config import")
    parser.add_argument("--db-path", type=str, default="/opt/OSTG/ai_knowledge_base.db",
                       help="Path to knowledge base database")
    
    args = parser.parse_args()
    
    try:
        from utils.ai import (
            ConfigKnowledgeBase, 
            NetworkConfigParser,
            import_device_configs_from_ostg
        )
    except ImportError as e:
        logger.error(f"Failed to import AI modules: {e}")
        logger.error("Make sure you're running from the OSTG directory")
        sys.exit(1)
    
    kb = ConfigKnowledgeBase(db_path=args.db_path)
    parser_obj = NetworkConfigParser()
    
    imported_count = 0
    
    # Option 1: Import from OSTG database
    if args.import_ostg:
        logger.info("Importing configurations from OSTG database...")
        try:
            import_device_configs_from_ostg(knowledge_base=kb)
            logger.info("✅ Successfully imported configurations from OSTG")
            imported_count += 1
        except Exception as e:
            logger.error(f"Failed to import from OSTG: {e}")
    
    # Option 2: Import from directory
    if args.config_dir:
        logger.info(f"Importing configurations from directory: {args.config_dir}")
        config_dir = Path(args.config_dir)
        if not config_dir.exists():
            logger.error(f"Directory not found: {args.config_dir}")
            return
        
        config_files = list(config_dir.glob("*.conf")) + list(config_dir.glob("*.txt")) + list(config_dir.glob("*.cfg"))

        # v0.5.245-followup (audit AI-*): the previous loop passed the file
        # stem as both device_id AND device_name, so router1.conf and
        # router1.cfg (or router1.txt) collided on the primary key inside
        # ConfigKnowledgeBase -- the second import silently overwrote the
        # first (or raised, depending on the backend). Give each file a
        # unique device_id derived from stem + extension while keeping the
        # human-readable device_name as the stem alone.
        seen_ids: Dict[str, Path] = {}
        for config_file in config_files:
            try:
                config_text = config_file.read_text()
                device_name = config_file.stem
                # Extension without leading dot; falls back to 'cfg' if none.
                ext = config_file.suffix.lstrip(".").lower() or "cfg"
                device_id = f"{device_name}-{ext}"
                if device_id in seen_ids:
                    logger.warning(
                        f"Duplicate device_id '{device_id}' from {config_file} "
                        f"(already imported from {seen_ids[device_id]}); skipping."
                    )
                    continue
                seen_ids[device_id] = config_file

                vendor = args.vendor if args.vendor != "auto" else parser_obj.detect_vendor(config_text)

                kb.add_config(device_id, device_name, config_text, vendor=vendor)
                imported_count += 1
                logger.info(f"✅ Imported: {device_name} as {device_id} ({vendor})")
            except Exception as e:
                logger.error(f"Failed to import {config_file}: {e}")
    
    # Option 3: Import single file
    if args.config_file:
        config_file = Path(args.config_file)
        if not config_file.exists():
            logger.error(f"File not found: {args.config_file}")
            return
        
        try:
            config_text = config_file.read_text()
            device_id = args.device_id or config_file.stem
            device_name = args.device_name or config_file.stem
            vendor = args.vendor if args.vendor != "auto" else parser_obj.detect_vendor(config_text)
            
            kb.add_config(device_id, device_name, config_text, vendor=vendor)
            imported_count += 1
            logger.info(f"✅ Imported: {device_name} ({vendor})")
        except Exception as e:
            logger.error(f"Failed to import {config_file}: {e}")
    
    if imported_count == 0:
        logger.warning("No configurations were imported. Use --help for usage options.")
    else:
        logger.info(f"✅ Successfully imported {imported_count} configuration(s)")


if __name__ == "__main__":
    main()




